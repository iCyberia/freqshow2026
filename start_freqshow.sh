#!/usr/bin/env bash
sleep 5
export DISPLAY=:0
cd /home/hiroshi/FreqShow
exec /home/hiroshi/FreqShow-venv/bin/python3 /home/hiroshi/FreqShow/freqshow.py >> /home/hiroshi/freqshow.out 2>&1
