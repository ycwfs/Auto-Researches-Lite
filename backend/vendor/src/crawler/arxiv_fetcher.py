"""
arXiv 论文爬取器

使用 arxiv API 获取指定领域的最新论文
"""
import arxiv
import fcntl
import logging
import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import List, Dict, Any
from pathlib import Path

from src.utils import (
    build_paper_set_signature,
    get_current_datetime,
    get_paper_identity,
    get_date_string,
    get_data_path,
    load_json,
    normalize_paper_pdf_url,
    save_json,
)


class ArxivFetcher:
    """arXiv 论文爬取器"""
    
    def __init__(self, config: Dict[str, Any]):
        """初始化
        
        Args:
            config: 配置字典
        """
        self.config = config
        self.arxiv_config = config.get('arxiv', {})
        self.logger = logging.getLogger('daily_arxiv.fetcher')
        
        # 获取配置
        self.categories = self.arxiv_config.get('categories', ['cs.AI'])
        self.keywords = self.arxiv_config.get('keywords', [])
        self.max_results = self.arxiv_config.get('max_results', 20)
        self.sort_by = self.arxiv_config.get('sort_by', 'submittedDate')
        self.sort_order = self.arxiv_config.get('sort_order', 'descending')
        self.last_fetch_stats: Dict[str, Any] = {
            'days_back': None,
            'raw_count': 0,
            'new_count': 0,
            'duplicate_count': 0,
            'existing_today_count': 0,
            'saved_count': 0,
        }
        
    def build_query(self) -> str:
        """构建搜索查询
        
        Returns:
            查询字符串
        """
        # 构建类别查询
        if len(self.categories) == 1:
            category_query = f"cat:{self.categories[0]}"
        else:
            category_parts = [f"cat:{cat}" for cat in self.categories]
            category_query = "(" + " OR ".join(category_parts) + ")"
        
        # 如果有关键词，添加关键词过滤
        if self.keywords:
            # 构建关键词查询（在标题或摘要中搜索）
            keyword_parts = []
            for keyword in self.keywords:
                # 在标题和摘要中搜索关键词
                keyword_parts.append(f'(ti:"{keyword}" OR abs:"{keyword}")')
            keyword_query = "(" + " OR ".join(keyword_parts) + ")"
            
            # 组合类别和关键词
            query = f"{category_query} AND {keyword_query}"
        else:
            query = category_query
        
        self.logger.info(f"构建的查询: {query}")
        return query

    def _get_storage_dir(self) -> Path:
        """获取论文存储目录。"""
        return Path(get_data_path(self.config, 'papers'))

    @contextmanager
    def _daily_papers_lock(self, date_str: str):
        """对当天论文文件加锁，避免并发运行丢失数据。"""
        lock_path = self._get_storage_dir() / f"papers_{date_str}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        with open(lock_path, 'w', encoding='utf-8') as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _get_paper_key(self, paper: Dict[str, Any]) -> str:
        """生成用于历史去重的稳定论文标识。"""
        return get_paper_identity(paper)

    def _load_daily_papers(self, date_str: str) -> List[Dict[str, Any]]:
        """加载当天已经保留的论文。"""
        filepath = self._get_storage_dir() / f"papers_{date_str}.json"
        if not filepath.exists():
            return []

        data = load_json(str(filepath))
        if data is None:
            return []
        if not isinstance(data, list):
            raise ValueError(f"每日论文文件格式错误，期望列表: {filepath}")

        return data

    def _load_historical_paper_ids(self, date_str: str) -> set[str]:
        """加载历史已抓取论文的去重标识（不含当天文件）。"""
        data_path = self._get_storage_dir()
        if not data_path.exists():
            return set()

        current_filename = f"papers_{date_str}.json"
        history_ids = set()

        for filepath in sorted(data_path.glob("papers_*.json")):
            if filepath.name == current_filename:
                continue

            data = load_json(str(filepath))
            if data is None:
                continue
            if not isinstance(data, list):
                raise ValueError(f"历史论文文件格式错误，期望列表: {filepath}")

            for paper in data:
                if not isinstance(paper, dict):
                    raise ValueError(f"历史论文记录格式错误，期望对象: {filepath}")
                history_ids.add(self._get_paper_key(paper))

        return history_ids

    def _prepare_daily_papers(
        self,
        fetched_papers: List[Dict[str, Any]],
        date_str: str | None = None,
    ) -> Dict[str, Any]:
        """基于历史记录筛出真正的新论文，并合并当天已保留的论文。"""
        target_date = date_str or get_date_string(config=self.config)
        existing_today_papers = self._load_daily_papers(target_date)
        historical_ids = self._load_historical_paper_ids(target_date)

        normalized_today_papers = []
        existing_today_ids = set()
        for paper in existing_today_papers:
            if not isinstance(paper, dict):
                raise ValueError("当天论文记录格式错误，期望对象")
            paper = normalize_paper_pdf_url(paper)

            paper_key = self._get_paper_key(paper)
            if paper_key in historical_ids or paper_key in existing_today_ids:
                continue

            existing_today_ids.add(paper_key)
            normalized_today_papers.append(paper)

        new_papers = []
        seen_in_run = set()
        duplicate_count = 0

        for paper in fetched_papers:
            paper = normalize_paper_pdf_url(paper)
            paper_key = self._get_paper_key(paper)
            if (
                paper_key in historical_ids
                or paper_key in existing_today_ids
                or paper_key in seen_in_run
            ):
                duplicate_count += 1
                continue

            seen_in_run.add(paper_key)
            new_papers.append(paper)

        daily_papers = normalized_today_papers + new_papers
        return {
            'date': target_date,
            'new_papers': new_papers,
            'daily_papers': daily_papers,
            'raw_count': len(fetched_papers),
            'new_count': len(new_papers),
            'duplicate_count': duplicate_count,
            'existing_today_count': len(normalized_today_papers),
            'saved_count': len(daily_papers),
        }

    def get_daily_papers(self, date_str: str | None = None) -> List[Dict[str, Any]]:
        """读取当天保留的论文，并按当前去重规则规范化。"""
        target_date = date_str or get_date_string(config=self.config)
        if target_date == get_date_string(config=self.config):
            with self._daily_papers_lock(target_date):
                return self._prepare_daily_papers([], date_str=target_date)['daily_papers']
        return self._prepare_daily_papers([], date_str=target_date)['daily_papers']
    
    def fetch_papers(self, days_back: int = 1) -> List[Dict[str, Any]]:
        """获取论文
        
        Args:
            days_back: 获取过去几天的论文，默认1天
            
        Returns:
            论文列表
        """
        self.logger.info("=" * 60)
        self.logger.info("开始爬取 arXiv 论文")
        self.logger.info(f"类别: {', '.join(self.categories)}")
        if self.keywords:
            self.logger.info(f"关键词: {', '.join(self.keywords)}")
        self.logger.info(f"最大结果数: {self.max_results}")
        self.logger.info("=" * 60)
        
        # 构建查询
        query = self.build_query()
        
        # 设置排序方式
        sort_by_map = {
            'submittedDate': arxiv.SortCriterion.SubmittedDate,
            'relevance': arxiv.SortCriterion.Relevance,
            'lastUpdatedDate': arxiv.SortCriterion.LastUpdatedDate,
        }
        sort_criterion = sort_by_map.get(self.sort_by, arxiv.SortCriterion.SubmittedDate)
        
        sort_order_map = {
            'descending': arxiv.SortOrder.Descending,
            'ascending': arxiv.SortOrder.Ascending,
        }
        sort_order = sort_order_map.get(self.sort_order, arxiv.SortOrder.Descending)
        
        # 创建搜索对象
        search = arxiv.Search(
            query=query,
            max_results=self.max_results,
            sort_by=sort_criterion,
            sort_order=sort_order
        )
        
        # Bounded retries so a transient arXiv/CDN blip fails fast instead of
        # stalling discovery for minutes. Tunable via env; the app layer also
        # sets a per-request socket timeout (see integrations/sources/arxiv.py).
        client = arxiv.Client(
            page_size=min(self.max_results, 1000),
            delay_seconds=float(os.environ.get("ARXIV_DELAY_SECONDS", "3.0")),
            num_retries=int(os.environ.get("ARXIV_NUM_RETRIES", "2")),
        )

        # 获取论文
        papers = []
        cutoff_date = get_current_datetime(self.config) - timedelta(days=days_back)
        max_attempts = int(os.environ.get("ARXIV_FETCH_ATTEMPTS", "2"))

        for attempt in range(1, max_attempts + 1):
            try:
                self.logger.info("正在获取论文...")
                papers = []

                for result in client.results(search):
                    published = result.published
                    if cutoff_date.tzinfo is not None and published.tzinfo is not None:
                        published = published.astimezone(cutoff_date.tzinfo)
                    elif cutoff_date.tzinfo is None and published.tzinfo is not None:
                        published = published.replace(tzinfo=None)

                    if published < cutoff_date:
                        self.logger.debug(
                            f"论文 {result.title} 发布于 {result.published}，早于截止日期"
                        )
                        continue

                    paper = self._extract_paper_info(result)
                    papers.append(paper)
                    self.logger.info(f"✓ [{len(papers)}] {paper['title'][:60]}...")

                self.logger.info("=" * 60)
                date_str = get_date_string(config=self.config)
                with self._daily_papers_lock(date_str):
                    prepared_papers = self._prepare_daily_papers(papers)
                    self.last_fetch_stats = {
                        'days_back': days_back,
                        'raw_count': prepared_papers['raw_count'],
                        'new_count': prepared_papers['new_count'],
                        'duplicate_count': prepared_papers['duplicate_count'],
                        'existing_today_count': prepared_papers['existing_today_count'],
                        'saved_count': prepared_papers['saved_count'],
                    }

                    self.logger.info(
                        "原始抓取 %d 篇论文，过滤历史重复 %d 篇，本次新增 %d 篇",
                        prepared_papers['raw_count'],
                        prepared_papers['duplicate_count'],
                        prepared_papers['new_count'],
                    )
                    if prepared_papers['existing_today_count']:
                        self.logger.info(
                            "今日已保留 %d 篇论文，本次保存后累计 %d 篇",
                            prepared_papers['existing_today_count'],
                            prepared_papers['saved_count'],
                        )

                    if not prepared_papers['new_papers']:
                        self.logger.info("ℹ️ 本次没有新的未采集论文，跳过保存")
                        self.logger.info("=" * 60)
                        return []

                    self.logger.info(
                        f"✅ 本次新增 {prepared_papers['new_count']} 篇论文"
                    )
                    self.logger.info("=" * 60)

                    self._save_papers(prepared_papers['daily_papers'])
                    return prepared_papers['daily_papers']

            except arxiv.HTTPError as e:
                is_rate_limit = "HTTP 429" in str(e)
                if not is_rate_limit or attempt == max_attempts:
                    self.logger.error(f"❌ 获取论文失败: {e}", exc_info=True)
                    raise

                wait_seconds = min(
                    int(os.environ.get("ARXIV_RETRY_MAX_WAIT", "20")),
                    int(os.environ.get("ARXIV_RETRY_BASE_WAIT", "5")) * attempt,
                )
                self.logger.warning(
                    f"arXiv 返回 429，{wait_seconds} 秒后进行第 {attempt + 1} 次重试"
                )
                time.sleep(wait_seconds)

            except Exception as e:
                self.logger.error(f"❌ 获取论文失败: {e}", exc_info=True)
                raise        

        # # search until successful
        # try:
        #     self.logger.info("正在获取论文...")
        #     for result in search.results():
        #         # 检查提交日期
        #         if result.published.replace(tzinfo=None) < cutoff_date:
        #             self.logger.debug(f"论文 {result.title} 发布于 {result.published}，早于截止日期")
        #             continue
                
        #         # 提取论文信息
        #         paper = self._extract_paper_info(result)
        #         papers.append(paper)
                
        #         self.logger.info(f"✓ [{len(papers)}] {paper['title'][:60]}...")
            
        #     self.logger.info("=" * 60)
        #     self.logger.info(f"✅ 成功获取 {len(papers)} 篇论文")
        #     self.logger.info("=" * 60)
            
        #     # 保存论文数据
        #     self._save_papers(papers)
            
        #     return papers
            
        # except Exception as e:
        #     self.logger.error(f"❌ 获取论文失败: {str(e)}", exc_info=True)
        #     raise
    
    def _extract_paper_info(self, result: arxiv.Result) -> Dict[str, Any]:
        """提取论文信息
        
        Args:
            result: arxiv.Result 对象
            
        Returns:
            论文信息字典
        """
        return normalize_paper_pdf_url(
            {
            'id': result.entry_id.split('/')[-1],  # arXiv ID
            'title': result.title,
            'authors': [author.name for author in result.authors],
            'abstract': result.summary.replace('\n', ' ').strip(),
            'categories': result.categories,
            'primary_category': result.primary_category,
            'published': result.published.isoformat(),
            'updated': result.updated.isoformat(),
            'pdf_url': result.pdf_url,
            'entry_url': result.entry_id,
            'comment': result.comment if hasattr(result, 'comment') else None,
            'journal_ref': result.journal_ref if hasattr(result, 'journal_ref') else None,
            'doi': result.doi if hasattr(result, 'doi') else None,
            'fetched_at': get_current_datetime(self.config).isoformat(),
            }
        )
    
    def _save_papers(self, papers: List[Dict[str, Any]]):
        """保存论文数据
        
        Args:
            papers: 论文列表
        """
        if not papers:
            self.logger.warning("没有论文需要保存")
            return
        
        # 获取存储路径
        data_path = get_data_path(self.config, 'papers')
        Path(data_path).mkdir(parents=True, exist_ok=True)
        
        # 按日期保存
        date_str = get_date_string(config=self.config)
        filepath = f"{data_path}/papers_{date_str}.json"
        
        # 保存数据
        save_json(papers, filepath)
        self.logger.info(f"💾 论文数据已保存到: {filepath}")
        self.save_latest_snapshot(papers, date_str=date_str)

    def save_latest_snapshot(
        self,
        papers: List[Dict[str, Any]],
        date_str: str | None = None,
        run_id: str | None = None,
    ):
        """刷新 latest.json，使其与当天保留论文保持一致。"""
        if not papers:
            return

        data_path = get_data_path(self.config, 'papers')
        Path(data_path).mkdir(parents=True, exist_ok=True)
        target_date = date_str or get_date_string(config=self.config)
        latest_filepath = f"{data_path}/latest.json"
        save_json(
            {
                'date': target_date,
                'run_id': run_id or self.config.get('_runtime', {}).get('run_id'),
                'count': len(papers),
                'new_count': self.last_fetch_stats.get('new_count', len(papers)),
                'duplicate_count': self.last_fetch_stats.get('duplicate_count', 0),
                'paper_signature': build_paper_set_signature(papers),
                'papers': papers,
            },
            latest_filepath,
        )
        self.logger.info(f"💾 最新数据已保存到: {latest_filepath}")
    
    def get_paper_stats(self, papers: List[Dict[str, Any]]) -> Dict[str, Any]:
        """获取论文统计信息
        
        Args:
            papers: 论文列表
            
        Returns:
            统计信息字典
        """
        if not papers:
            return {}
        
        # 统计类别分布
        category_counts = {}
        for paper in papers:
            for category in paper['categories']:
                category_counts[category] = category_counts.get(category, 0) + 1
        
        # 统计作者数量
        author_counts = {}
        for paper in papers:
            for author in paper['authors']:
                author_counts[author] = author_counts.get(author, 0) + 1
        
        # 找出高产作者（发表2篇以上）
        prolific_authors = {k: v for k, v in author_counts.items() if v >= 2}
        
        stats = {
            'total_papers': len(papers),
            'category_distribution': category_counts,
            'total_authors': len(author_counts),
            'prolific_authors': prolific_authors,
            'date': get_date_string(config=self.config),
        }
        
        return stats
    
    def print_paper_summary(self, papers: List[Dict[str, Any]]):
        """打印论文摘要
        
        Args:
            papers: 论文列表
        """
        if not papers:
            self.logger.info("没有找到论文")
            return
        
        self.logger.info("\n" + "=" * 80)
        self.logger.info(f"📚 今日论文摘要 ({len(papers)} 篇)")
        self.logger.info("=" * 80)
        
        for i, paper in enumerate(papers, 1):
            self.logger.info(f"\n[{i}] {paper['title']}")
            self.logger.info(f"    作者: {', '.join(paper['authors'][:3])}" + 
                           (" et al." if len(paper['authors']) > 3 else ""))
            self.logger.info(f"    类别: {', '.join(paper['categories'][:3])}")
            self.logger.info(f"    链接: {paper['pdf_url']}")
            self.logger.info(f"    摘要: {paper['abstract'][:150]}...")
        
        # 显示统计信息
        stats = self.get_paper_stats(papers)
        self.logger.info("\n" + "=" * 80)
        self.logger.info("📊 统计信息")
        self.logger.info("=" * 80)
        self.logger.info(f"总论文数: {stats['total_papers']}")
        self.logger.info(f"总作者数: {stats['total_authors']}")
        
        if stats.get('prolific_authors'):
            self.logger.info("\n高产作者 (2篇以上):")
            for author, count in sorted(stats['prolific_authors'].items(), 
                                       key=lambda x: x[1], reverse=True)[:5]:
                self.logger.info(f"  - {author}: {count} 篇")
        
        self.logger.info("\n类别分布:")
        for category, count in sorted(stats['category_distribution'].items(), 
                                     key=lambda x: x[1], reverse=True):
            self.logger.info(f"  - {category}: {count} 篇")
        
        self.logger.info("=" * 80 + "\n")


def main():
    """测试函数"""
    from src.utils import load_config, load_env, setup_logging
    
    load_env()
    config = load_config()
    logger = setup_logging(config)
    
    fetcher = ArxivFetcher(config)
    papers = fetcher.fetch_papers(days_back=2)  # 获取过去2天的论文
    
    if papers:
        fetcher.print_paper_summary(papers)


if __name__ == "__main__":
    main()
