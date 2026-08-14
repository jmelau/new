#!/usr/bin/env bash
# Double-click this file in Finder to install. It just runs setup.sh in a
# Terminal window and keeps the window open afterwards so you can read the result.
cd "$(dirname "$0")"
./setup.sh
status=$?
echo
if [ $status -ne 0 ]; then
  echo "Setup stopped early — the message above says why. You can close this window."
else
  echo "You can close this window."
fi
printf "Press return to finish. "
read -r _
