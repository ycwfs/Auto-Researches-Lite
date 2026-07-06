"""工具函数模块"""
import hashlib
import os
import yaml
import json
import logging
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List
from urllib.parse import urlparse
from dotenv import load_dotenv
import pytz


PAPER_VERSION_PATTERN = re.compile(r"v\d+$")
ARXIV_HOSTS = {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}
ARXIV_PAPER_PATH_PATTERN = re.compile(r"^/(?:abs|pdf)/(?P<paper_id>[^?#/]+?)(?:\.pdf)?/?$")


def load_config(config_path: str = "config/config.yaml") -> Dict[str, Any]:
    """加载配置文件
    
    Args:
        config_path: 配置文件路径
        
    Returns:
        配置字典
    """
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    return config


def load_env():
    """加载环境变量"""
    load_dotenv()


def setup_logging(config: Dict[str, Any]) -> logging.Logger:
    """设置日志
    
    Args:
        config: 配置字典
        
    Returns:
        Logger 对象
    """
    log_config = config.get('logging', {})
    log_level = getattr(logging, log_config.get('level', 'INFO'))
    log_format = log_config.get('format', '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # 创建 logger
    logger = logging.getLogger('daily_arxiv')
    logger.setLevel(log_level)
    
    # 控制台处理器
    if log_config.get('console', True):
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)
        console_handler.setFormatter(logging.Formatter(log_format))
        logger.addHandler(console_handler)
    
    # 文件处理器
    log_file = log_config.get('file')
    if log_file:
        # 确保日志目录存在
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(log_level)
        file_handler.setFormatter(logging.Formatter(log_format))
        logger.addHandler(file_handler)
    
    return logger


def save_json(data: Any, filepath: str):
    """保存 JSON 数据
    
    Args:
        data: 要保存的数据
        filepath: 文件路径
    """
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        'w',
        encoding='utf-8',
        dir=path.parent,
        prefix=f"{path.name}.",
        suffix='.tmp',
        delete=False,
    ) as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        temp_path = Path(f.name)
    os.replace(temp_path, path)


def save_text(content: str, filepath: str):
    """原子化保存文本文件。"""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        'w',
        encoding='utf-8',
        dir=path.parent,
        prefix=f"{path.name}.",
        suffix='.tmp',
        delete=False,
    ) as f:
        f.write(content)
        temp_path = Path(f.name)
    os.replace(temp_path, path)


def load_json(filepath: str) -> Any:
    """加载 JSON 数据
    
    Args:
        filepath: 文件路径
        
    Returns:
        加载的数据
    """
    if not os.path.exists(filepath):
        return None
    
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)


def get_current_datetime(config: Dict[str, Any] | None = None) -> datetime:
    """获取当前时间，优先使用调度器配置的时区。"""
    runtime_datetime = None
    if config:
        runtime_datetime = config.get('_runtime', {}).get('run_datetime')
    if isinstance(runtime_datetime, datetime):
        return runtime_datetime

    timezone = ""
    if config:
        timezone = config.get('scheduler', {}).get('timezone', '').strip()

    if timezone:
        return datetime.now(pytz.timezone(timezone))

    return datetime.now()


def get_date_string(date: datetime = None, config: Dict[str, Any] | None = None) -> str:
    """获取日期字符串
    
    Args:
        date: datetime 对象，默认为当前日期
        config: 配置字典，可用于获取调度器时区
        
    Returns:
        格式化的日期字符串 YYYY-MM-DD
    """
    if date is None:
        date = get_current_datetime(config)
    return date.strftime('%Y-%m-%d')


def get_data_path(config: Dict[str, Any], subdir: str = 'papers') -> str:
    """获取数据存储路径
    
    Args:
        config: 配置字典
        subdir: 子目录名称
        
    Returns:
        数据路径
    """
    storage_config = config.get('storage', {})
    base_path = storage_config.get('json_path', 'data/papers')
    
    if subdir == 'summaries':
        return 'data/summaries'
    
    return base_path


def get_paper_identity(paper: Dict[str, Any]) -> str:
    """获取用于论文集合比较的稳定标识。"""
    paper_id = str(paper.get('id') or '').strip()
    if not paper_id:
        entry_url = str(paper.get('entry_url') or '').strip()
        if entry_url:
            paper_id = entry_url.rstrip('/').split('/')[-1]

    if not paper_id:
        raise ValueError(f"论文缺少可用于识别的标识: {paper}")

    return PAPER_VERSION_PATTERN.sub('', paper_id)


def normalize_arxiv_pdf_url(pdf_url: str | None, entry_url: str | None = None) -> str:
    """将 arXiv PDF 链接规范化为带 .pdf 后缀的稳定形式。"""
    for candidate in [str(pdf_url or '').strip(), str(entry_url or '').strip()]:
        if not candidate:
            continue

        parsed = urlparse(candidate)
        if parsed.netloc.lower() not in ARXIV_HOSTS:
            continue

        match = ARXIV_PAPER_PATH_PATTERN.match(parsed.path)
        if not match:
            continue

        paper_id = match.group('paper_id')
        return f"https://arxiv.org/pdf/{paper_id}.pdf"

    return str(pdf_url or '').strip()


def normalize_paper_pdf_url(paper: Dict[str, Any]) -> Dict[str, Any]:
    """返回带规范化 PDF 链接的论文对象副本。"""
    normalized_paper = dict(paper)
    normalized_paper['pdf_url'] = normalize_arxiv_pdf_url(
        paper.get('pdf_url'),
        paper.get('entry_url'),
    )
    return normalized_paper


def build_paper_set_signature(papers: List[Dict[str, Any]]) -> str:
    """为一组论文生成稳定签名，用于判断下游产物是否过期。"""
    identities = sorted({get_paper_identity(paper) for paper in papers})
    joined = '\n'.join(identities)
    return hashlib.sha256(joined.encode('utf-8')).hexdigest()
