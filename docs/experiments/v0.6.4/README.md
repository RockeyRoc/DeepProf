# DeepProf v0.6.4 public experiment evidence

Latest batch: `m3-fulltext-rag-20261004-v4`, 120 questions × four conditions = 480 valid cells. The 347-page OCR source and 629-chunk index remain private. Retrieval labels: 13,710; citation labels: 1,595; answerability labels: 120. These are AI annotations; genuine personnel review is pending.

NoMIRACL Chinese: 3,770 fixed-candidate queries and 37,599 scored pairs; test FAR 4.51%, FRR 70.11%, AUC 0.8007. This evaluates ranking and evidence acceptance, not whole-corpus recall or generated-answer correctness.

BKT experiments use ASSISTments student-grouped nested development OOF. Candidate calibration/item parameters remain exploratory; the runtime defaults are unchanged and no student learning improvement is established.

The Chinese report, figures, numeric/label aggregate CSVs, plotting sources, frozen dependency lock and model revision/hash metadata are included. Public NoMIRACL query/document IDs are dataset identifiers. Raw prompts, provider messages, textbook excerpts, private databases, credentials, downloaded models and local absolute paths are excluded. Original experiment bytes, private lineage and historical failed/stopped batches are retained in the local archive.

Run `python scripts/build_public_evidence.py --check --bundle` to verify and rebuild the release evidence ZIP. For figure regeneration, install ggplot2/svglite/ragg/jsonlite and run the four `figures/*.R` scripts from the repository root; their default input directory is this public export. Historical M1–M3 evidence remains under the existing experiment report links.
