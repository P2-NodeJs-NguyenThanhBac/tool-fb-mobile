#!/data/data/com.termux/files/usr/bin/bash
echo STARTED > "/sdcard/Download/termux_clipboard_probe_timeout.txt"
timeout 5 termux-clipboard-get > "/sdcard/Download/termux_clipboard_value_timeout.txt" 2> "/sdcard/Download/termux_clipboard_value_timeout.err"
printf %s $? > "/sdcard/Download/termux_clipboard_value_timeout.exit"
