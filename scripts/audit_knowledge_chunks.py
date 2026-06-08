"""One-off audit of knowledge_chunks table quality."""
import os
import sys

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from supabase import create_client


def is_garbled(text: str) -> bool:
    if not text:
        return True
    sample = text[:500]
    repl = sample.count("\ufffd")
    printable = sum(1 for ch in sample if ch.isprintable() or ch in "\n\t\r")
    letters = sum(1 for ch in sample if ch.isalpha())
    if repl > 3:
        return True
    if len(sample) > 0 and printable / len(sample) < 0.88:
        return True
    if letters < 20 and len(sample) > 50:
        return True
    return False


def main() -> None:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    sb = create_client(url, key)

    rows: list[dict] = []
    offset = 0
    while True:
        batch = (
            sb.table("knowledge_chunks")
            .select("id,site_id,source_url,title,content,chunk_index,embedding,created_at")
            .range(offset, offset + 999)
            .execute()
            .data
            or []
        )
        if not batch:
            break
        rows.extend(batch)
        offset += 1000
        if len(batch) < 1000:
            break

    total = len(rows)
    null_emb = sum(1 for r in rows if r.get("embedding") is None)
    uploads = sum(
        1 for r in rows if "/wp-content/uploads/" in (r.get("source_url") or "").lower()
    )
    garbled = [r for r in rows if is_garbled(r.get("content", ""))]
    good_emb = total - null_emb
    sites: dict[str, dict[str, int]] = {}
    for r in rows:
        sid = r["site_id"]
        sites.setdefault(sid, {"total": 0, "null_emb": 0, "garbled": 0})
        sites[sid]["total"] += 1
        if r.get("embedding") is None:
            sites[sid]["null_emb"] += 1
        if is_garbled(r.get("content", "")):
            sites[sid]["garbled"] += 1

    print("=== AUDIT knowledge_chunks ===")
    print(f"Total chunks: {total}")
    if total:
        print(f"Avec embedding: {good_emb} ({100 * good_emb / total:.1f}%)")
        print(f"Sans embedding (NULL): {null_emb} ({100 * null_emb / total:.1f}%)")
    print(f"URLs wp-content/uploads: {uploads}")
    if total:
        print(f"Contenu illisible/garbled: {len(garbled)} ({100 * len(garbled) / total:.1f}%)")
    print()
    print("Par site_id:")
    for sid, s in sites.items():
        print(f"  {sid[:8]}... total={s['total']} null_emb={s['null_emb']} garbled={s['garbled']}")
    print()
    print("Exemples GARBLED (max 3):")
    for r in garbled[:3]:
        c = (r.get("content") or "")[:80].replace("\n", " ")
        print(f"  [{r['chunk_index']}] {r['source_url'][:70]}")
        print(f"    content: {repr(c)}")
    print()
    print("Exemples OK (max 3):")
    ok = [r for r in rows if not is_garbled(r.get("content", "")) and r.get("embedding")][:3]
    for r in ok:
        c = (r.get("content") or "")[:100].replace("\n", " ")
        print(f"  [{r['chunk_index']}] {r['source_url'][:70]}")
        print(f"    content: {c}...")


if __name__ == "__main__":
    main()
