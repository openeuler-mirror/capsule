import re


def get_markdown_images(markdown_text: str) -> list[str]:
    """Extract image links from markdown without importing render dependencies."""
    pattern = r'!\[[^\]]*\]\((.*?)\)'
    matches = re.findall(pattern, markdown_text)

    image_links = []
    for match in matches:
        url = match.split(" ")[0].strip()
        if url:
            image_links.append(url)

    return image_links
