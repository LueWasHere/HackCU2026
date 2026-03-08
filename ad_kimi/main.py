import asyncio
from browser_use import Agent, Browser


KIMI_URL = "https://kimi.moonshot.cn"


async def generate_slideshow(prompt: str, profile_path: str):

    browser = Browser(headless=False, user_data_dir=profile_path)

    agent = Agent(
        browser=browser,
        task=f"""
Go to {KIMI_URL}

Create a slideshow presentation about:

{prompt}

Requirements:
- 8 to 10 slides
- title for each slide
- bullet points
- speaker notes

When finished, download the slides.
"""
    )

    result = await agent.run()

    return result


async def main():

    slides = await generate_slideshow(
        "The future of robotics in healthcare",
        "./profile"
    )

    print(slides)


if __name__ == "__main__":
    asyncio.run(main())
