"""
Promotion module for finding and managing new users to follow.
"""
import logging
from typing import List, Set
from datetime import datetime

from .models import PromotedUser, Config
from .github_client import GitHubClient
from .utils import load_promoted_users, save_promoted_users, get_data_dir


logger = logging.getLogger(__name__)


class PromotionManager:
    """Manages user promotion for expanding network."""
    
    def __init__(self, client: GitHubClient, config: Config):
        """
        Initialize promotion manager.
        
        Args:
            client: GitHub API client
            config: Application configuration
        """
        self.client = client
        self.config = config
        self.promoted_users_file = get_data_dir() / "promoted_users.txt"
        
    async def find_users_to_promote(
        self,
        current_followers: Set[str],
        current_following: Set[str],
        ban_list: Set[str],
        count: int,
    ) -> List[str]:
        """
        Find new users to follow using circle-affinity scoring.

        Instead of grabbing random followers-of-random-followers, this looks at who
        the people in *your own circle* follow, and ranks candidates by how many
        circle members connect to them. A candidate followed by several people in
        your circle is very likely "in the same circle" as you — a much more
        natural target than a random stranger.

        Circle seeds are chosen in order of tightness: mutuals (you follow each
        other) first, then accounts you follow, then your followers.

        Args:
            current_followers: Set of your current followers
            current_following: Set of accounts you currently follow
            ban_list: Set of users to exclude
            count: Number of users to find

        Returns:
            List of usernames to promote, ranked by circle affinity.
        """
        import random
        from collections import Counter

        if count <= 0:
            return []

        excluded = ban_list | current_following | current_followers | {self.config.username}

        # 1. Build circle seeds, tightest first.
        mutuals = list(current_followers & current_following)
        following_only = list(current_following - current_followers)
        followers_only = list(current_followers - current_following)
        for group in (mutuals, following_only, followers_only):
            random.shuffle(group)
        seed_pool = mutuals + following_only + followers_only

        if not seed_pool:
            logger.info("No circle seeds available for promotion search")
            return []

        seeds_count = min(self.config.seeds_count, len(seed_pool))
        seeds = seed_pool[:seeds_count]
        logger.info(f"Circle search: {len(seeds)} seeds "
                    f"({len(mutuals)} mutuals available)")

        # 2. Sample each seed's following list (friends-of-friends = your circle).
        pages_per_seed = max(1, int(self.config.pages_per_seed))
        max_random_page = max(1, int(self.config.max_random_page))
        pages_map = {
            u: sorted(random.sample(
                range(1, max_random_page + 1),
                k=min(pages_per_seed, max_random_page)
            ))
            for u in seeds
        }

        try:
            following_dict = await self.client.get_following_batch(seeds, pages_map=pages_map)
        except Exception as e:
            logger.warning(f"Error sampling circle: {e}")
            return []

        # 3. Score candidates by circle overlap (how many seeds connect to them).
        scores: Counter = Counter()
        for connections in following_dict.values():
            for cand in set(connections):  # dedupe within a single seed
                if cand not in excluded:
                    scores[cand] += 1

        if not scores:
            logger.info("Circle search found no new candidates")
            return []

        # 4. Keep candidates meeting the min affinity; relax if the quota can't fill.
        min_score = max(1, int(self.config.affinity_min_score))
        ranked = [(c, s) for c, s in scores.items() if s >= min_score]
        if len(ranked) < count and min_score > 1:
            logger.info(f"Only {len(ranked)} candidates at affinity>={min_score}; "
                        f"relaxing threshold to fill quota")
            ranked = list(scores.items())

        # Random tiebreak among equally-connected candidates: shuffle, then stable
        # sort by score so equal scores keep their randomized order.
        random.shuffle(ranked)
        ranked.sort(key=lambda x: x[1], reverse=True)

        result = [c for c, _ in ranked[:count]]
        top_score = ranked[0][1] if ranked else 0
        logger.info(f"Circle search: {len(scores)} candidates scored, "
                    f"selected {len(result)} (top affinity={top_score}, min={min_score})")
        return result
        
    async def check_and_update_promoted_users(self, persist: bool = True) -> tuple[List[str], List[str]]:
        """
        Check promoted users and separate active from expired.

        Args:
            persist: When False, do not rewrite the tracking file (read-only use,
                e.g. statistics or dry-run).

        Returns:
            Tuple of (active_promoted_users, expired_promoted_users)
        """
        promoted_users = await load_promoted_users(self.promoted_users_file)

        active = []
        expired = []

        for user in promoted_users:
            if user.is_expired(self.config.days_period):
                expired.append(user.username)
            else:
                active.append(user.username)

        logger.info(f"Promoted users: {len(active)} active, {len(expired)} expired")

        # Prune expired entries from the tracking file, unless read-only.
        if persist:
            active_promoted = [u for u in promoted_users if not u.is_expired(self.config.days_period)]
            await save_promoted_users(self.promoted_users_file, active_promoted)

        return active, expired
        
    async def add_promoted_users(self, usernames: List[str]):
        """
        Add new promoted users to tracking.
        
        Args:
            usernames: List of usernames to add
        """
        if not usernames:
            return
            
        # Load existing promoted users
        existing = await load_promoted_users(self.promoted_users_file)
        existing_usernames = {u.username for u in existing}
        
        # Add new users
        now = datetime.now()
        for username in usernames:
            if username not in existing_usernames:
                existing.append(PromotedUser(username=username, promotion_date=now))
                
        # Save updated list
        await save_promoted_users(self.promoted_users_file, existing)
        logger.info(f"Added {len(usernames)} new promoted users")
        
    async def process_promotion(
        self,
        current_followers: Set[str],
        current_following: Set[str],
        ban_list: Set[str]
    ) -> tuple[Set[str], Set[str]]:
        """
        Process promotion logic and return updated follower/following sets.
        
        Args:
            current_followers: Current followers set
            current_following: Current following set
            ban_list: Ban list
            
        Returns:
            Tuple of (updated_followers, updated_following)
        """
        if not self.config.promotion:
            logger.info("Promotion is disabled")
            return current_followers, current_following
            
        logger.info("Processing promotion...")
        
        # Check existing promoted users (don't mutate the tracking file in dry-run)
        active_promoted, expired_promoted = await self.check_and_update_promoted_users(
            persist=not self.config.dry_run
        )
        
        # Add active promoted to followers (so we don't unfollow them)
        updated_followers = current_followers | set(active_promoted)
        
        # Remove expired promoted from following (they will be unfollowed)
        updated_following = current_following - set(expired_promoted)
        
        # Find new users to promote if needed
        current_promoted_count = len(active_promoted)
        needed_count = self.config.count_promotion_users - current_promoted_count

        # Never discover more than one run's follow budget at a time: keeps API
        # usage and follow volume small on automated server runs.
        if self.config.max_follows_per_run > 0:
            needed_count = min(needed_count, self.config.max_follows_per_run)

        if needed_count > 0:
            logger.info(f"Need to find {needed_count} new users to promote")
            
            new_promoted = await self.find_users_to_promote(
                current_followers,
                current_following,
                ban_list,
                needed_count,
            )

            if new_promoted:
                # Track new promoted users (skip persistence in dry-run)
                if not self.config.dry_run:
                    await self.add_promoted_users(new_promoted)

                # Add to followers set (so we follow them)
                updated_followers.update(new_promoted)

                logger.info(f"Added {len(new_promoted)} new users for promotion")
        else:
            logger.info(f"Already have {current_promoted_count} promoted users, no new ones needed")
            
        return updated_followers, updated_following
