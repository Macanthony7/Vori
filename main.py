#!/usr/bin/env python3
"""
Crypto Service Main Entry Point
Run this via cron every 1-5 minutes
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from utils import setup_logger, log_info, log_error
from datetime import datetime
import time

logger = setup_logger('main')

def run_task(task_name, task_function):
    """Run a task and log results"""
    log_info(logger, f"--- Running {task_name} ---")
    start_time = time.time()
    
    try:
        result = task_function()
        elapsed = time.time() - start_time
        log_info(logger, f"{task_name} completed in {elapsed:.2f}s. Result: {result}")
        return True
    except Exception as e:
        elapsed = time.time() - start_time
        log_error(logger, f"{task_name} failed after {elapsed:.2f}s: {e}")
        return False

def main():
    """Main execution flow"""
    log_info(logger, "=" * 60)
    log_info(logger, f"CRYPTO SERVICE STARTED - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log_info(logger, "=" * 60)
    
    start_time = time.time()
    
    # Import task modules
    try:
        from webhook_processor import process_all_webhooks
        from sweeper import process_sweeps
        from fallback_watcher import run_fallback_scan
        from generator import check_and_refill_all_chains
        from manual_actions import process_pending_actions
        from poller import main as run_poller_cycle
    except ImportError as e:
        log_error(logger, f"Failed to import modules: {e}")
        return 1
    
    # Task execution order
    # Note: Poller runs its own internal 5-minute loop (30 x 10s)
    tasks = [
        ("Manual Actions", process_pending_actions),
        ("Webhook Processor", process_all_webhooks),
        ("Sweeper", process_sweeps),
        ("Fallback Watcher", run_fallback_scan),
        ("Address Generator", check_and_refill_all_chains),
        ("Payment Poller", run_poller_cycle),      # NEW: 10-sec polling loop
    ]
    
    success_count = 0
    for task_name, task_func in tasks:
        if run_task(task_name, task_func):
            success_count += 1
    
    # Summary
    total_time = time.time() - start_time
    log_info(logger, "=" * 60)
    log_info(logger, f"CRYPTO SERVICE COMPLETED - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log_info(logger, f"Tasks completed: {success_count}/{len(tasks)}")
    log_info(logger, f"Total execution time: {total_time:.2f}s")
    log_info(logger, "=" * 60)
    
    return 0 if success_count == len(tasks) else 1

if __name__ == '__main__':
    sys.exit(main())