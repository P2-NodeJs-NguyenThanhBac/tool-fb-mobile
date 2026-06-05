import json

from util.get_device_info import get_all_devices_from_mongo

OUTPUT_FILE = "device_map.json"


def main():
    result = {}

    for item in get_all_devices_from_mongo():
        device_name = str(item.get("device_name", "")).strip()
        device_id = str(item.get("device_id", "")).strip()

        if device_name and device_id:
            result[device_name] = device_id

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Da tao file {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
