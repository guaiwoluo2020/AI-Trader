#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from datetime import datetime


def build_system_status_payload(
    pending_instructions: int,
    statistics_records: int,
    symbols,
    latest_statistics=None,
):
    now = datetime.now()
    last_statistics_at = None
    mt5_connected = False

    if latest_statistics and getattr(latest_statistics, "timestamp", None):
        last_statistics_at = latest_statistics.timestamp.isoformat()
        mt5_connected = (now - latest_statistics.timestamp).total_seconds() <= 30

    return {
        "status": "ok",
        "pending_instructions": pending_instructions,
        "statistics_records": statistics_records,
        "symbols": symbols,
        "mt5_connected": mt5_connected,
        "last_statistics_at": last_statistics_at,
        "system": {
            "version": "2.0.0",
            "uptime": None,
            "memory": None,
        },
    }
