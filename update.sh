#!/bin/sh
echo -n "Update started at "
date
echo "Running market updater" &&
timeout 10m ./market-stuff.py 4-07MU EX6-AO && \
echo "Market updater finished" &&
scp id_list.js market.css *.html sound_market:
