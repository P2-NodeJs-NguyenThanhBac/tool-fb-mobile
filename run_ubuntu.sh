#!/bin/bash

# Đường dẫn tới venv
VENV_PATH="./tool_venv"

# Kích hoạt venv và chạy từng script trong terminal riêng biệt
gnome-terminal -- bash -c "source $VENV_PATH/bin/activate && python3 main.py; exec bash"
gnome-terminal -- bash -c "source $VENV_PATH/bin/activate && python3 _local.py; exec bash"
gnome-terminal -- bash -c "source $VENV_PATH/bin/activate && python3 zalo_crm_main.py; exec bash"
gnome-terminal -- bash -c "source $VENV_PATH/bin/activate && python3 zalo_crm_base.py; exec bash"