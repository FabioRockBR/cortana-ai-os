"""
Batch ingest documents into Qdrant via split-pdf service.
Usage: python3 batch_ingest.py --dir /path/to/docs --cf-url http://localhost:8080 --cf-token TOKEN
"""
import argparse, base64, os, glob, requests, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

def ingest_file(path, cf_url, cf_token, workers=3):
    fname = os.path.basename(path)
    ext = os.path.splitext(fname)[1].lower()
    mime_map = {'.pdf': 'application/pdf', '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg', '.png': 'image/png',
                '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}
    mime = mime_map.get(ext, 'application/octet-stream')

    size = os.path.getsize(path)
    if size > 20 * 1024 * 1024 and ext == '.pdf':
        # Large PDF — split into chunks first
        return ingest_large_pdf(path, cf_url, cf_token)

    with open(path, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode()

    r = requests.post(cf_url,
        headers={'X-CF-Token': cf_token, 'Content-Type': 'application/json'},
        json={'pdfBase64': b64, 'fileName': fname, 'mimeType': mime},
        timeout=360)
    if r.ok:
        d = r.json()
        return True, d.get('chunks_stored', d.get('num_chunks', '?'))
    return False, r.text[:100]


def ingest_large_pdf(path, cf_url, cf_token, pages_per_chunk=10):
    """Split large PDF into chunks and ingest each."""
    import tempfile, subprocess
    fname = os.path.basename(path)
    tmpdir = tempfile.mkdtemp()

    # Split using pymupdf
    try:
        import fitz
        doc = fitz.open(path)
        total = doc.page_count
        chunk_paths = []
        for start in range(0, total, pages_per_chunk):
            end = min(start + pages_per_chunk, total)
            chunk_fname = f"{os.path.splitext(fname)[0]}_chunk_{start//pages_per_chunk+1:03d}_p{start+1:04d}-{end:04d}.pdf"
            chunk_path = os.path.join(tmpdir, chunk_fname)
            chunk_doc = fitz.open()
            chunk_doc.insert_pdf(doc, from_page=start, to_page=end-1)
            chunk_doc.save(chunk_path)
            chunk_doc.close()
            chunk_paths.append(chunk_path)
        doc.close()
    except ImportError:
        return False, "pymupdf not installed (pip install pymupdf)"

    ok = 0
    for cp in chunk_paths:
        success, msg = ingest_file(cp, cf_url, cf_token)
        if success:
            ok += 1

    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)
    return ok == len(chunk_paths), f"{ok}/{len(chunk_paths)} chunks"


def main():
    parser = argparse.ArgumentParser(description='Batch ingest documents into Qdrant')
    parser.add_argument('--dir', required=True, help='Directory containing documents')
    parser.add_argument('--cf-url', default='http://localhost:8080', help='split-pdf service URL')
    parser.add_argument('--cf-token', required=True, help='CF_AUTH_TOKEN value')
    parser.add_argument('--workers', type=int, default=3, help='Concurrent uploads (default: 3)')
    parser.add_argument('--pattern', default='*', help='Glob pattern (default: *)')
    args = parser.parse_args()

    patterns = ['*.pdf', '*.PDF', '*.jpg', '*.jpeg', '*.png', '*.xlsx']
    files = []
    for pat in patterns:
        files.extend(glob.glob(os.path.join(args.dir, pat)))
    files = sorted(set(files))

    if not files:
        print(f'No files found in {args.dir}')
        sys.exit(1)

    print(f'Found {len(files)} files in {args.dir}')
    print(f'Using {args.workers} workers → {args.cf_url}')
    print()

    ok = 0; errors = []

    def process(args_tuple):
        i, path = args_tuple
        fname = os.path.basename(path)
        size_mb = os.path.getsize(path) / 1024 / 1024
        success, msg = ingest_file(path, args.cf_url, args.cf_token)
        return i, fname, size_mb, success, msg

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(process, (i+1, p)): p for i, p in enumerate(files)}
        for fut in as_completed(futures):
            i, fname, size_mb, success, msg = fut.result()
            if success:
                print(f'  ✅ [{i}/{len(files)}] {fname} ({size_mb:.1f}MB) → {msg} chunks')
                ok += 1
            else:
                print(f'  ❌ [{i}/{len(files)}] {fname} ({size_mb:.1f}MB) → {msg}')
                errors.append(fname)

    print(f'\nDone: {ok}/{len(files)} succeeded, {len(errors)} failed')
    if errors:
        print('Failed files:')
        for e in errors:
            print(f'  {e}')


if __name__ == '__main__':
    main()
