import asyncio
import uiautomator2 as u2

from module.crawl_post_comments import crawl_post_comments

DEVICE_ID = "7HYP4T4XTS4DXKCY"  # sửa lại device của bạn
POST_LINK = "https://www.facebook.com/share/p/1EExbJmV9q/"

async def main():
    driver = await asyncio.to_thread(u2.connect_usb, DEVICE_ID)

    result = await crawl_post_comments(
        driver,
        command_id=None,
        post_link=POST_LINK,
        include_profile_link=False,   # test nhanh trước
        max_comments=50,              # test ít comment trước
        replace_existing=True,
        back_to_facebook=True,
    )
    print(result)

if __name__ == "__main__":
    asyncio.run(main())