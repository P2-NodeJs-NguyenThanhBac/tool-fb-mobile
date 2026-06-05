@echo off
cd /d D:\tool-fb-mobile\tool-fb-mobile - Copy - stagging
pm2 stop FB_ZL_Nuoi
pm2 start FB_ZL_Dang_tin
pm2 save