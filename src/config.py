"""
Configuration management module.
"""
import os
import re
import yaml
import logging
from pathlib import Path
from typing import Optional, Set, List, Dict, Any
import aiofiles

from .models import Config


def _resolve_token(raw: Optional[str]) -> str:
    """
    Resolve the GitHub token, preferring environment variables over the config file.

    Precedence:
      1. GITHUB_TOKEN / SUBMANAGER_TOKEN environment variables (never stored on disk).
      2. ${VAR} placeholder inside the config value, expanded from the environment.
      3. The literal value in the config file.
    """
    env_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("SUBMANAGER_TOKEN")
    if env_token:
        return env_token.strip()

    if raw:
        match = re.fullmatch(r"\$\{([A-Z0-9_]+)\}", raw.strip())
        if match:
            return os.environ.get(match.group(1), "").strip()
        return raw.strip()

    return ""


logger = logging.getLogger(__name__)


class ConfigManager:
    """Manages application configuration."""
    
    def __init__(self, config_path: Optional[Path] = None):
        """
        Initialize configuration manager.

        Args:
            config_path: Path to configuration file. Falls back to the
                SUBMANAGER_CONFIG environment variable, then to .env.yaml next
                to the project root.
        """
        env_path = os.environ.get("SUBMANAGER_CONFIG")
        self.config_path = (
            config_path
            or (Path(env_path) if env_path else None)
            or Path(__file__).parent.parent / ".env.yaml"
        )
        self.config: Optional[Config] = None
        self.ban_lists: Dict[str, Set[str]] = {
            'never_follow': set(),
            'never_unfollow': set(),
            'ignore_completely': set()
        }
        
    async def load(self) -> Config:
        """
        Load configuration from YAML file asynchronously.
        
        Returns:
            Loaded configuration object
            
        Raises:
            FileNotFoundError: If configuration file doesn't exist
            yaml.YAMLError: If configuration file is invalid
        """
        if not self.config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")
            
        try:
            async with aiofiles.open(self.config_path, 'r', encoding='utf-8') as f:
                content = await f.read()
                data = yaml.safe_load(content)
            
            # Convert YAML structure to Config
            promotion_cfg = data.get('promotion', {})
            settings_cfg = data.get('settings', {})

            config_data = {
                'USERNAME': data['github']['username'],
                'TOKEN': _resolve_token(data['github'].get('token')),
                'PROMOTION': promotion_cfg.get('enabled', True),
                'DAYS_PERIOD': promotion_cfg.get('days_period', 3),
                'COUNT_PROMOTION_USERS': promotion_cfg.get('count_users', 50),
                'RETRY_ON': settings_cfg.get('retry_on_error', True),
                # New promotion discovery tuning
                'SEEDS_COUNT': promotion_cfg.get('seeds_count', 5),
                'PAGES_PER_SEED': promotion_cfg.get('pages_per_seed', 2),
                'MAX_RANDOM_PAGE': promotion_cfg.get('max_random_page', 5),
                'AFFINITY_MIN_SCORE': promotion_cfg.get('affinity_min_score', 1),
                # Small-scale safety limits
                'MAX_FOLLOWS_PER_RUN': settings_cfg.get('max_follows_per_run', 20),
                'MAX_UNFOLLOWS_PER_RUN': settings_cfg.get('max_unfollows_per_run', 20),
                'MIN_ACTION_DELAY': settings_cfg.get('min_action_delay', 2.0),
                'MAX_ACTION_DELAY': settings_cfg.get('max_action_delay', 6.0),
                'DRY_RUN': settings_cfg.get('dry_run', False),
            }
            
            self.config = Config.from_dict(config_data)
            
            # Load ban lists
            ban_lists = data.get('ban_lists', {})
            self.ban_lists['never_follow'] = set(ban_lists.get('never_follow') or [])
            self.ban_lists['never_unfollow'] = set(ban_lists.get('never_unfollow') or [])
            self.ban_lists['ignore_completely'] = set(ban_lists.get('ignore_completely') or [])
            
            logger.info(f"Configuration loaded from {self.config_path}")
            logger.info(f"Ban lists: {len(self.ban_lists['never_follow'])} never_follow, "
                       f"{len(self.ban_lists['never_unfollow'])} never_unfollow, "
                       f"{len(self.ban_lists['ignore_completely'])} ignore_completely")
            
            # Validate configuration
            self._validate_config()
            
            return self.config
            
        except yaml.YAMLError as e:
            logger.error(f"Invalid YAML in configuration file: {e}")
            raise
        except KeyError as e:
            logger.error(f"Missing required configuration key: {e}")
            raise
        except Exception as e:
            logger.error(f"Error loading configuration: {e}")
            raise
            
    def _validate_config(self):
        """Validate loaded configuration."""
        if not self.config:
            raise ValueError("Configuration not loaded")
            
        if not self.config.username:
            raise ValueError("USERNAME is required in configuration")
            
        if not self.config.token:
            raise ValueError("TOKEN is required in configuration")
            
        if self.config.token.startswith("ghp_") and len(self.config.token) != 40:
            logger.warning("GitHub token appears to be invalid format")
            
        if self.config.days_period < 1:
            raise ValueError("DAYS_PERIOD must be at least 1")
            
        if self.config.count_promotion_users < 0:
            raise ValueError("COUNT_PROMOTION_USERS cannot be negative")
        
        # Validate new promotion discovery settings
        if self.config.seeds_count < 1:
            raise ValueError("seeds_count must be >= 1")
        if self.config.pages_per_seed < 1:
            raise ValueError("pages_per_seed must be >= 1")
        if self.config.max_random_page < 1:
            raise ValueError("max_random_page must be >= 1")
        if self.config.affinity_min_score < 1:
            raise ValueError("affinity_min_score must be >= 1")

        # Small-scale safety limits
        if self.config.max_follows_per_run < 0:
            raise ValueError("max_follows_per_run cannot be negative")
        if self.config.max_unfollows_per_run < 0:
            raise ValueError("max_unfollows_per_run cannot be negative")
        if self.config.min_action_delay < 0 or self.config.max_action_delay < 0:
            raise ValueError("action delays cannot be negative")
        if self.config.max_action_delay < self.config.min_action_delay:
            raise ValueError("max_action_delay must be >= min_action_delay")
            
    async def save(self, config: Optional[Config] = None):
        """
        Save configuration to YAML file asynchronously.
        
        Args:
            config: Configuration object to save (uses current if not provided)
        """
        if config:
            self.config = config
            
        if not self.config:
            raise ValueError("No configuration to save")
            
        data = {
            'github': {
                'username': self.config.username,
                'token': self.config.token
            },
'promotion': {
                'enabled': self.config.promotion,
                'days_period': self.config.days_period,
                'count_users': self.config.count_promotion_users,
                # Promotion discovery tuning
                'seeds_count': self.config.seeds_count,
                'pages_per_seed': self.config.pages_per_seed,
                'max_random_page': self.config.max_random_page,
                'affinity_min_score': self.config.affinity_min_score,
            },
            'settings': {
                'retry_on_error': self.config.retry_on,
                'max_concurrent_requests': 5,
                'request_delay': 0.5,
                'batch_size': 5,
                'max_follows_per_run': self.config.max_follows_per_run,
                'max_unfollows_per_run': self.config.max_unfollows_per_run,
                'min_action_delay': self.config.min_action_delay,
                'max_action_delay': self.config.max_action_delay,
                'dry_run': self.config.dry_run,
            },
            'ban_lists': {
                'never_follow': sorted(list(self.ban_lists.get('never_follow', set()))),
                'never_unfollow': sorted(list(self.ban_lists.get('never_unfollow', set()))),
                'ignore_completely': sorted(list(self.ban_lists.get('ignore_completely', set())))
            },
            'logging': {
                'level': 'INFO',
                'file': 'subscription_manager.log'
            }
        }
        
        async with aiofiles.open(self.config_path, 'w', encoding='utf-8') as f:
            yaml_content = yaml.dump(data, default_flow_style=False, sort_keys=False)
            await f.write(yaml_content)
            
        logger.info(f"Configuration saved to {self.config_path}")
        
    def get(self) -> Config:
        """
        Get current configuration.
        
        Returns:
            Current configuration object
            
        Raises:
            ValueError: If configuration not loaded
        """
        if not self.config:
            raise ValueError("Configuration not loaded. Call load() first.")
        return self.config
        
    async def reload(self) -> Config:
        """
        Reload configuration from file.
        
        Returns:
            Reloaded configuration object
        """
        logger.info("Reloading configuration...")
        return await self.load()
    
    def get_ban_lists(self) -> Dict[str, Set[str]]:
        """
        Get ban lists.
        
        Returns:
            Dictionary with ban lists
        """
        return self.ban_lists
    
    def get_combined_ban_list_followers(self) -> Set[str]:
        """
        Get combined ban list for followers (never_follow + ignore_completely).
        
        Returns:
            Set of usernames to never follow
        """
        return self.ban_lists['never_follow'] | self.ban_lists['ignore_completely']
    
    def get_combined_ban_list_following(self) -> Set[str]:
        """
        Get combined ban list for following (never_unfollow).
        
        Returns:
            Set of usernames to never unfollow
        """
        return self.ban_lists['never_unfollow']


# Global configuration manager instance
config_manager = ConfigManager()
