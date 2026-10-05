DeepProf v0.6.4 brings the local CLI/Gateway and browser chat/study workspace together with a curated BKT/RAG experiment export.

- Browser sessions support chat and explicitly labelled non-experiment study, document/image uploads, streamed replies, cancellation, model/reasoning controls and source inspection.
- Provider-aware native web search and deep-research reports distinguish a successful request from verified returned sources.
- Release packaging reads the package version, validates the checked-out tag, and publishes SHA-256 manifests alongside the CLI archive and public experiment bundle.

The latest full-text RAG batch contains 480 valid cells across 120 questions and four conditions, with 13,710 retrieval, 1,595 citation and 120 answerability AI labels. NoMIRACL Chinese evaluates 3,770 queries and 37,599 fixed-candidate pairs (test FAR 4.51%, FRR 70.11%, AUC 0.8007). These measurements do not establish student learning gain; genuine personnel review remains pending. BKT research candidates remain exploratory and do not replace the runtime development defaults. Historical failures and stopped batches remain preserved.

Assets: `deepprof-cli-0.6.4.tgz`, its checksum, a release-wide SHA-256 manifest, the Chinese Markdown/PDF report, a public evidence ZIP and its file manifest. Raw prompts, textbook pages, private databases and credentials are excluded. Node.js 22+ and Python 3.12+ are required; first setup needs Python package-index access. Existing user data and provider configuration are retained.

```sh
npm install --global https://github.com/RockeyRoc/DeepProf/releases/download/v0.6.4/deepprof-cli-0.6.4.tgz
deepprof doctor
deepprof web
```

中文版本、接口与实验边界详见仓库 `docs/RELEASEv0.6.4.md`。人员复核与真实学生学习效果仍未完成；本版发布的是工程功能和可核对的实验记录。
