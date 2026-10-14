# KhataLens

Photograph a handwritten udhaar (credit) notebook page. Get a clean ledger and one-tap
WhatsApp payment reminders. **Everything runs on the shopkeeper's own computer** using an
open-weight vision-language model served by Ollama. No customer data leaves the machine.

## Quick start

```bash
pip install -r requirements.txt
ollama pull qwen2.5vl:7b          # or any vision model your hardware can run
python scripts/synth_ledger.py --n 20 --out data/synth    # sample pages for the demo
streamlit run app.py
```

Click **Try a sample page** to test the review -> save -> remind flow with no model, or
upload a real photo and click **Read this page**. Use another model with
`KHATALENS_MODEL=<name>` or in the sidebar (this is where your fine-tuned model plugs in).

## Layout

| Path | Purpose |
|---|---|
| `khatalens/extract.py` | Image -> JSON via local Ollama, with structured-output schema |
| `khatalens/schema.py` | Validation: Devanagari digits, dates, flags suspicious rows |
| `khatalens/preprocess.py` | Size, lighting (CLAHE), deskew |
| `khatalens/db.py` | SQLite ledger, balances, overdue filter |
| `khatalens/reminders.py` | Reminder text (hi/mr/ta/en) + `wa.me` links (no WhatsApp API) |
| `scripts/synth_ledger.py` | Synthetic handwritten-style pages + LLaMA-Factory training file |
| `scripts/evaluate.py` | Field-level accuracy: open base vs fine-tuned vs closed API |
| `app.py` | Streamlit UI |

## Evaluate (the table for your write-up)

```bash
python scripts/evaluate.py --labels data/test/labels.jsonl --backend ollama --model qwen2.5vl:7b
python scripts/evaluate.py --labels data/test/labels.jsonl --backend ollama --model khatalens-ft
python scripts/evaluate.py --labels data/test/labels.jsonl --backend anthropic --model <closed-model>
```

Put your own handwritten pages in `data/test/` (same `labels.jsonl` format). **Never train on
them.** Synthetic pages are for training; real/own handwriting is for testing.

## Status

- [x] Schema, validation, DB, reminders, preprocessing (tested)
- [x] Synthetic data generator, evaluation harness (scoring tested with an oracle)
- [x] Streamlit review UI (smoke-tested headlessly)
- [ ] Run base model on real handwriting, record baseline  <- next
- [ ] LoRA fine-tune on synthetic + own pages
- [ ] Three-way comparison, first real user, write-up
