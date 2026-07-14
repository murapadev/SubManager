"""
Main subscription management module.
"""
import logging
import asyncio
import random
from pathlib import Path
from typing import Set, Dict, List

from .models import Config, SubscriptionState
from .github_client import GitHubClient
from .promotion import PromotionManager


logger = logging.getLogger(__name__)


class SubscriptionManager:
    """Manages GitHub subscriptions with async operations."""
    
    def __init__(self, config: Config, config_manager=None):
        """
        Initialize subscription manager.
        
        Args:
            config: Application configuration
            config_manager: Configuration manager instance
        """
        self.config = config
        self.config_manager = config_manager
        self.client = GitHubClient(config.username, config.token)
        self.promotion_manager = PromotionManager(self.client, config)
        
        # File paths (kept for backward compatibility)
        self.base_path = Path(__file__).parent.parent
        
        self.state = SubscriptionState()
        
    async def __aenter__(self):
        """Async context manager entry."""
        await self.client.__aenter__()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.client.__aexit__(exc_type, exc_val, exc_tb)
        
    async def load_ban_lists(self):
        """Load ban lists from configuration."""
        logger.info("Loading ban lists...")
        
        if not self.config_manager:
            raise ValueError("ConfigManager is required for loading ban lists")
        
        # Load from YAML config
        # ban_list_followers = users we should NOT follow (never_follow + ignore_completely)
        self.state.ban_list_followers = self.config_manager.get_combined_ban_list_followers()
        # ban_list_following = users we should NOT unfollow (never_unfollow)
        self.state.ban_list_following = self.config_manager.get_combined_ban_list_following()
        
        logger.info(f"Loaded {len(self.state.ban_list_followers)} users to never follow, {len(self.state.ban_list_following)} users to never unfollow")
        
    async def fetch_current_state(self):
        """Fetch current followers and following from GitHub."""
        logger.info("Fetching current subscription state from GitHub...")
        
        # Fetch followers and following concurrently
        followers_task = self.client.get_followers()
        following_task = self.client.get_following()
        
        followers, following = await asyncio.gather(followers_task, following_task)
        
        # Filter out banned users
        self.state.followers = set(followers) - self.state.ban_list_followers
        self.state.following = set(following) - self.state.ban_list_following
        
        logger.info(f"Current state: {len(self.state.followers)} followers, {len(self.state.following)} following")
        
    async def process_subscriptions(self):
        """Process subscriptions: follow/unfollow users."""
        logger.info("Processing subscriptions...")
        
        # Process promotion if enabled
        if self.config.promotion:
            updated_followers, updated_following = await self.promotion_manager.process_promotion(
                self.state.followers,
                self.state.following,
                self.state.ban_list_followers
            )
            self.state.followers = updated_followers
            self.state.following = updated_following
        
        # Calculate users to follow and unfollow
        users_to_follow = self.state.get_users_to_follow()
        users_to_unfollow = self.state.get_users_to_unfollow()

        logger.info(f"Candidates -> follow: {len(users_to_follow)}, unfollow: {len(users_to_unfollow)}")

        # Enforce small-scale per-run caps. Randomize which candidates are picked so
        # repeated runs don't always act on the same alphabetical prefix.
        follow_batch = self._cap(users_to_follow, self.config.max_follows_per_run, "follow")
        unfollow_batch = self._cap(users_to_unfollow, self.config.max_unfollows_per_run, "unfollow")

        if self.config.dry_run:
            logger.info("[DRY-RUN] No changes will be made to GitHub")
            logger.info(f"[DRY-RUN] Would follow {len(follow_batch)}: {sorted(follow_batch)}")
            logger.info(f"[DRY-RUN] Would unfollow {len(unfollow_batch)}: {sorted(unfollow_batch)}")
            return

        # Process follows and unfollows concurrently
        results = await asyncio.gather(
            self._process_follows(follow_batch),
            self._process_unfollows(unfollow_batch),
            return_exceptions=True
        )
        
        # Log any errors
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Error during subscription processing: {result}")
                
    def _cap(self, candidates: Set[str], limit: int, action: str) -> List[str]:
        """Randomly cap a candidate set to the per-run limit for small-scale use."""
        pool = list(candidates)
        if limit <= 0 or len(pool) <= limit:
            return sorted(pool)
        picked = random.sample(pool, limit)
        logger.info(f"Capping {action}: {len(pool)} candidates -> {limit} this run")
        return sorted(picked)

    async def _run_sequential(self, users: List[str], op, verb: str) -> Dict[str, bool]:
        """
        Run follow/unfollow one user at a time with a randomized human-like delay.

        Sequential + jittered pacing keeps volume low and avoids the burst pattern
        that GitHub abuse detection flags on server deployments.
        """
        if not users:
            return {}

        logger.info(f"{verb.capitalize()}ing {len(users)} users (min={self.config.min_action_delay}s, "
                    f"max={self.config.max_action_delay}s)...")

        results: Dict[str, bool] = {}
        for idx, user in enumerate(users, start=1):
            results[user] = await op(user)
            logger.info(f"Progress: {idx}/{len(users)} {verb}ed")
            if idx < len(users):
                await asyncio.sleep(random.uniform(self.config.min_action_delay,
                                                    self.config.max_action_delay))

        successful = sum(1 for v in results.values() if v)
        logger.info(f"Successfully {verb}ed {successful}/{len(users)} users")
        return results

    async def _process_follows(self, users: List[str]) -> Dict[str, bool]:
        """Follow the given users sequentially with jittered pacing."""
        return await self._run_sequential(users, self.client.follow_user, "follow")

    async def _process_unfollows(self, users: List[str]) -> Dict[str, bool]:
        """Unfollow the given users sequentially with jittered pacing."""
        return await self._run_sequential(users, self.client.unfollow_user, "unfollow")
        
    async def run(self):
        """Run the complete subscription management process."""
        try:
            # Load ban lists
            await self.load_ban_lists()
            
            # Fetch current state from GitHub
            await self.fetch_current_state()
            
            # Process subscriptions
            await self.process_subscriptions()
            
            logger.info("Subscription management completed successfully")
            
        except Exception as e:
            logger.error(f"Error during subscription management: {e}")
            raise
            
        
    async def get_statistics(self) -> Dict[str, int]:
        """
        Get current statistics.
        
        Returns:
            Dictionary with statistics
        """
        await self.fetch_current_state()
        
        stats = {
            "followers": len(self.state.followers),
            "following": len(self.state.following),
            "mutual": len(self.state.followers & self.state.following),
            "not_following_back": len(self.state.following - self.state.followers),
            "not_followed_back": len(self.state.followers - self.state.following),
            "banned_followers": len(self.state.ban_list_followers),
            "banned_following": len(self.state.ban_list_following),
        }
        
        if self.config.promotion:
            # Statistics are read-only: don't mutate the tracking file here.
            promoted_users = await self.promotion_manager.check_and_update_promoted_users(persist=False)
            stats["promoted_active"] = len(promoted_users[0])
            stats["promoted_expired"] = len(promoted_users[1])
            
        return stats
