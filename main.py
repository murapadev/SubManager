#!/usr/bin/env python3
"""
SubManager - Async GitHub Subscription Manager
Enhanced version with concurrent operations for better performance.
"""

import asyncio
import os
import sys
import argparse
import logging
from pathlib import Path

# Add src directory to path
sys.path.insert(0, str(Path(__file__).parent))

from src import (
    ConfigManager,
    SubscriptionManager,
    setup_logging,
    print_logo,
    check_internet_connection
)


logger = logging.getLogger(__name__)


async def main_async(config_path=None, dry_run=False):
    """Main async function."""
    # Setup logging
    setup_logging()
    
    # Print logo
    print_logo()
    
    logger.info("Starting SubManager v2.0 - Async Edition")
    print("🚀 SubManager v2.0 - Async Edition")
    print("=" * 60)
    
    # Check internet connection
    print("🔍 Checking internet connection...")
    if not await check_internet_connection():
        print("❌ No internet connection detected. Please check your connection.")
        logger.error("No internet connection")
        return 1
    print("✅ Internet connection OK")
    
    # Load configuration
    print("📋 Loading configuration...")
    config_manager = ConfigManager(config_path)

    try:
        config = await config_manager.load()
        if dry_run:
            config.dry_run = True
        if config.dry_run:
            print("🧪 DRY-RUN mode: no changes will be made to GitHub")
        print(f"✅ Configuration loaded for user: {config.username}")
        logger.info(f"Configuration loaded for user: {config.username}")
    except FileNotFoundError:
        print("❌ Configuration file not found: config.json")
        logger.error("Configuration file not found")
        return 1
    except Exception as e:
        print(f"❌ Error loading configuration: {e}")
        logger.error(f"Error loading configuration: {e}")
        return 1
    
    # Create subscription manager
    try:
        async with SubscriptionManager(config, config_manager) as manager:
            print("=" * 60)
            print("🔄 Starting subscription management...")
            print()
            
            # Run the main process
            await manager.run()
            
            print()
            print("=" * 60)
            
            # Get and display statistics
            stats = await manager.get_statistics()
            
            print("📊 Final Statistics:")
            print(f"  👥 Followers: {stats['followers']}")
            print(f"  ➡️  Following: {stats['following']}")
            print(f"  🤝 Mutual: {stats['mutual']}")
            print(f"  ❌ Not following back: {stats['not_following_back']}")
            print(f"  ⏳ Not followed back: {stats['not_followed_back']}")
            
            if config.promotion:
                print(f"  🚀 Active promoted: {stats.get('promoted_active', 0)}")
                print(f"  ⏰ Expired promoted: {stats.get('promoted_expired', 0)}")
            
            print("=" * 60)
            print("✅ SubManager completed successfully!")
            logger.info("SubManager completed successfully")
            
    except Exception as e:
        print(f"❌ Error during execution: {e}")
        logger.error(f"Error during execution: {e}", exc_info=True)
        return 1
    
    return 0


async def stats_command(config_path=None):
    """Show statistics only."""
    setup_logging()

    print("📊 SubManager Statistics")
    print("=" * 60)

    # Load configuration
    config_manager = ConfigManager(config_path)
    
    try:
        config = await config_manager.load()
    except Exception as e:
        print(f"❌ Error loading configuration: {e}")
        return 1
    
    try:
        async with SubscriptionManager(config, config_manager) as manager:
            stats = await manager.get_statistics()
            
            print(f"👤 User: {config.username}")
            print(f"👥 Followers: {stats['followers']}")
            print(f"➡️  Following: {stats['following']}")
            print(f"🤝 Mutual: {stats['mutual']}")
            print(f"❌ Not following back: {stats['not_following_back']}")
            print(f"⏳ Not followed back: {stats['not_followed_back']}")
            print(f"🚫 Banned followers: {stats['banned_followers']}")
            print(f"🚫 Banned following: {stats['banned_following']}")
            
            if config.promotion:
                print(f"🚀 Active promoted: {stats.get('promoted_active', 0)}")
                print(f"⏰ Expired promoted: {stats.get('promoted_expired', 0)}")
                
    except Exception as e:
        print(f"❌ Error getting statistics: {e}")
        return 1
    
    return 0




def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="SubManager - Async GitHub Subscription Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                    # Run subscription management
  python main.py --stats            # Show statistics only
        """
    )
    
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show statistics only"
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=os.environ.get("SUBMANAGER_CONFIG"),
        help="Path to the YAML config file (or set SUBMANAGER_CONFIG)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log intended follow/unfollow actions without touching GitHub"
    )

    args = parser.parse_args()
    config_path = Path(args.config) if args.config else None

    # Determine which command to run
    if args.stats:
        exit_code = asyncio.run(stats_command(config_path))
    else:
        exit_code = asyncio.run(main_async(config_path, args.dry_run))

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
