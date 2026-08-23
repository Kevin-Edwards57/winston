#!/usr/bin/env bash
pkill -f winston_app.py 2>/dev/null && echo "Winston stopped." || echo "Winston was not running."
