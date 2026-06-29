#!/usr/bin/env bash
# 同步 WSL wiki-bot 到 Windows 桌面分享包
# 运行: bash ~/wiki-bot/sync_to_desktop.sh

SRC="$HOME/wiki-bot"
DST="/mnt/c/Users/hsyhi/Desktop/WikiBot分享包(调试用)"

echo "同步中..."
cp "$SRC"/*.py "$SRC"/*.yaml "$SRC"/*.txt "$SRC"/*.bat "$SRC"/.*.gitignore "$SRC"/VERSION "$SRC"/config.example.yaml "$DST/" 2>/dev/null
cp "$SRC"/data/*.json "$DST"/data/ 2>/dev/null
echo "✓ 已同步到 $DST"
ls "$DST"/data/*.json 2>/dev/null | while read f; do
    echo "  $(basename $f) ($(du -h "$f" | cut -f1))"
done
