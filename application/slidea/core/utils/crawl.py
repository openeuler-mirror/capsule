import os
import asyncio
import tempfile
import re
import requests
from urllib.parse import unquote

from langchain_unstructured import UnstructuredLoader

from core.utils.logger import logger



def download_pdf_content(url):
    """下载PDF文件内容到临时文件"""
    try:
        response = requests.get(
            url,
            stream=True,
            timeout=60,
            allow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
            }
        )
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").split("; ")[0].strip()
        if content_type != "application/pdf":
            return None
        
        file_name = None
        cd_header = response.headers.get("Content-Disposition", "")
        if cd_header:
            match = re.search(r'filename\*?=[\'"]([^\'"]+)[\'"]', cd_header)
            if match:
                file_name = unquote(match.group(1).strip())

        if not file_name:
            file_name = f"download_{hash(url) & 0xFFFFFFFF:08x}.pdf"

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_file:
            temp_file_path = tmp_file.name
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    tmp_file.write(chunk)

        logger.debug(f"[下载] 完成 | 路径={temp_file_path} | 大小={os.path.getsize(temp_file_path)}")
        return temp_file_path
    except requests.exceptions.RequestException as e:
        logger.debug(f"[下载] 失败 | URL={url} | 网络请求失败={str(e)}")
    except Exception as e:
        logger.debug(f"[下载] 失败 | URL={url} | 错误={str(e)}")
    
    return None


async def get_content(file_path: str):
    """get content of document"""
    import logging
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    logging.getLogger("unstructured").setLevel(logging.ERROR)
    from pathlib import Path

    loader = None
    logger.info(f"read content {file_path}")
    try:
        if not Path(file_path).is_file():
            # UnstructuredLoader处理pdf依赖unstructured[pdf]包，过于庞大，通过pdfminer处理
            pdf_file = download_pdf_content(file_path)
            if pdf_file:
                file_path = pdf_file

        if not Path(file_path).is_file():
            loader = UnstructuredLoader(web_url=file_path, strategy="fast",
                                        include_metadata=False,
                                        languages=["zh", "eng"])
        else:
            if file_path.endswith(".md"):
                with open(file_path, "r", encoding="utf-8") as f:
                    full_text = f.read()
                    return full_text
            elif file_path.endswith(".pdf"):
                from pdfminer.high_level import extract_text
                full_text = extract_text(file_path)
                logger.info(f"get {file_path} content success: {len(full_text)}")
                return full_text
            else:
                loader = UnstructuredLoader(file_path=file_path, strategy="fast",
                                        include_metadata=False,
                                        languages=["zh", "eng"])

        if loader:
            async with asyncio.timeout(60):
                data = await loader.aload()
                full_text = "\n".join([doc.page_content for doc in data])
                logger.info(f"get {file_path} content success: {len(full_text)}")
                return full_text
    except Exception as e:
        logger.warning(f"get file content of {file_path} failed {e}")
        return ""


if __name__ == "__main__":
    print(asyncio.run(get_content("https://arxiv.org/pdf/2310.08560")))
