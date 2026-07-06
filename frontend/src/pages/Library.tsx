import { BookMarked, ExternalLink, FileText, FolderOpen, Library as LibraryIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { SectionHeading } from "../components/layout/AppShell";
import { EmptyState, Eyebrow, IconTile, PageLoader, Spinner } from "../components/ui";
import { api, type ZoteroCollection, type ZoteroItem } from "../lib/api";
import { useLang } from "../lib/lang";

export default function Library() {
  const { t } = useLang();
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [collections, setCollections] = useState<ZoteroCollection[]>([]);
  const [items, setItems] = useState<ZoteroItem[]>([]);
  const [activeCol, setActiveCol] = useState<string | null>(null);
  const [selected, setSelected] = useState<ZoteroItem | null>(null);
  const [loadingItems, setLoadingItems] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api.zoteroStatus().then((s) => {
      setConfigured(s.configured);
      if (s.configured) {
        api.zoteroCollections().then(setCollections).catch((e) => setError(e.message));
        void loadItems(null);
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadItems = async (col: string | null) => {
    setLoadingItems(true);
    setActiveCol(col);
    setSelected(null);
    try {
      setItems(await api.zoteroItems(col ?? undefined));
    } catch (e) {
      setError(e instanceof Error ? e.message : t("Failed to load items", "加载条目失败"));
    } finally {
      setLoadingItems(false);
    }
  };

  if (configured === null) return <PageLoader />;

  if (!configured)
    return (
      <div className="animate-fade-up">
        <SectionHeading eyebrow={t("Library", "文献库")} title={t("Zotero library", "Zotero 文献库")} />
        <EmptyState
          icon={<BookMarked size={32} />}
          title={t("Connect your Zotero account", "连接您的 Zotero 账户")}
          hint={t("Add your Zotero API key and library ID in Settings to browse your collections and items here, and to sync discovered papers.", "在设置中添加您的 Zotero API 密钥和文献库 ID，即可在此浏览您的分类和条目，并同步发现的论文。")}
          action={<Link to="/app/settings" className="btn-primary mt-2">{t("Go to Settings", "前往设置")}</Link>}
        />
      </div>
    );

  return (
    <div className="animate-fade-up">
      <SectionHeading eyebrow={t("Library", "文献库")} title={t("Zotero library", "Zotero 文献库")}
        desc={t("Browse your Zotero collections and items in a familiar layout.", "以熟悉的布局浏览您的 Zotero 分类和条目。")} />
      {error && (
        <div className="mb-3 rounded-lg border border-rose-500/20 bg-rose-500/10 px-3 py-2 text-sm text-rose-300">
          {error}
        </div>
      )}

      <div className="card grid h-[70vh] grid-cols-[230px,1fr,320px] overflow-hidden p-0">
        {/* Collections */}
        <div className="overflow-y-auto border-r border-white/[0.06] p-2.5">
          <button
            onClick={() => loadItems(null)}
            className={`flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors ${
              activeCol === null
                ? "bg-iris-500/15 text-white shadow-inner ring-1 ring-inset ring-iris-500/30"
                : "text-mist-300 hover:bg-white/5"
            }`}
          >
            <LibraryIcon size={15} className={activeCol === null ? "text-iris-300" : "text-mist-500"} />
            <span className="flex-1 text-left">{t("All items", "全部条目")}</span>
          </button>
          <div className="mt-3 mb-1 px-3 text-[11px] font-medium uppercase tracking-[0.08em] text-mist-500">
            {t("Collections", "分类")}
          </div>
          {collections.map((c) => {
            const on = activeCol === c.key;
            return (
              <button
                key={c.key}
                onClick={() => loadItems(c.key)}
                className={`flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors ${
                  on
                    ? "bg-iris-500/15 text-white shadow-inner ring-1 ring-inset ring-iris-500/30"
                    : "text-mist-300 hover:bg-white/5"
                }`}
              >
                <FolderOpen size={15} className={`shrink-0 ${on ? "text-iris-300" : "text-mist-500"}`} />
                <span className="flex-1 truncate text-left">{c.name}</span>
                <span className="font-mono text-[11px] text-mist-500">{c.num_items}</span>
              </button>
            );
          })}
        </div>

        {/* Items */}
        <div className="overflow-y-auto border-r border-white/[0.06]">
          {loadingItems ? (
            <div className="flex h-full items-center justify-center text-sm text-mist-500">
              <Spinner className="mr-2" /> {t("Loading…", "加载中…")}
            </div>
          ) : items.length === 0 ? (
            <div className="flex h-full items-center justify-center text-sm text-mist-500">{t("No items", "暂无条目")}</div>
          ) : (
            <table className="w-full text-sm">
              <thead className="sticky top-0 z-10 bg-ink-900/80 text-left text-[11px] font-medium uppercase tracking-[0.08em] text-mist-500 backdrop-blur">
                <tr className="border-b border-white/[0.06]">
                  <th className="px-4 py-2.5">{t("Title", "标题")}</th>
                  <th className="px-4 py-2.5">{t("Type", "类型")}</th>
                  <th className="px-4 py-2.5">{t("Date", "日期")}</th>
                </tr>
              </thead>
              <tbody>
                {items.map((it) => {
                  const on = selected?.key === it.key;
                  return (
                    <tr
                      key={it.key}
                      onClick={() => setSelected(it)}
                      className={`cursor-pointer border-b border-white/[0.04] transition-colors ${
                        on ? "bg-iris-500/10" : "hover:bg-white/[0.03]"
                      }`}
                    >
                      <td className={`px-4 py-2.5 ${on ? "text-white" : "text-mist-100"}`}>
                        {it.title || t("(untitled)", "（无标题）")}
                      </td>
                      <td className="px-4 py-2.5 text-mist-500">{it.item_type}</td>
                      <td className="px-4 py-2.5 font-mono text-[11px] text-mist-500">{it.date?.slice(0, 10)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        {/* Detail */}
        <div className="overflow-y-auto p-5">
          {selected ? (
            <div className="space-y-4">
              <Eyebrow icon={<BookMarked size={13} />}>{t("Item details", "条目详情")}</Eyebrow>
              <div className="flex items-start gap-3">
                <IconTile icon={<FileText size={16} />} tone="iris" size="sm" />
                <div className="min-w-0 flex-1">
                  <span className="text-[11px] font-medium uppercase tracking-[0.08em] text-mist-500">
                    {selected.item_type}
                  </span>
                  <h3 className="mt-1 font-display text-[15px] font-semibold leading-snug text-white">
                    {selected.title || t("(untitled)", "（无标题）")}
                  </h3>
                </div>
              </div>
              {selected.creators.length > 0 && (
                <div className="text-sm text-mist-300">{selected.creators.join(", ")}</div>
              )}
              {selected.date && (
                <div className="flex items-center justify-between border-t border-white/[0.06] pt-3 text-sm">
                  <span className="text-mist-500">{t("Date", "日期")}</span>
                  <span className="font-mono text-[12px] text-mist-100">{selected.date.slice(0, 10)}</span>
                </div>
              )}
              {selected.abstract && (
                <p className="text-sm leading-relaxed text-mist-500">{selected.abstract}</p>
              )}
              {selected.url && (
                <a
                  href={selected.url}
                  target="_blank"
                  rel="noreferrer"
                  className="btn-subtle mt-2 w-full justify-center"
                >
                  <ExternalLink size={15} /> {t("Open link", "打开链接")}
                </a>
              )}
            </div>
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
              <IconTile icon={<BookMarked size={18} />} tone="iris" size="md" />
              <span className="text-sm text-mist-500">{t("Select an item to see details", "选择一个条目以查看详情")}</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
