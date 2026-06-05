#!/data/data/com.termux/files/usr/bin/bash
echo STARTED > "/sdcard/Download/termux_clipboard_probe.txt"
termux-clipboard-get > "/sdcard/Download/termux_clipboard_value.txt" 2> "/sdcard/Download/termux_clipboard_value.err"
printf %s $? > "/sdcard/Download/termux_clipboard_value.exit"
