# Project page maintenance

The public project page is served at <https://qi202.github.io/SoMem/>.
GitHub Pages publishes `main` → `/docs`. The `.nojekyll` file makes this a static
site: no build tool, JavaScript, or external font/icon service is required.

- `index.html`: paper title, authors, approved abstract, method, results, and citation.
- `site.css`: desktop and mobile layouts.
- `assets/somem-agent.png`: approved Agent icon without a wordmark, used by the page, favicon, and README.
- `assets/document-format.png`: rendered from the manuscript's document-format figure.
- `assets/somem-overview.png` and `.pdf`: framework illustration shared with the README.

Preview from the repository root with `python -m http.server 8769 --directory docs`.
Changes pushed to `main` are published by GitHub Pages. Confirm the Pages build
finishes and the live URL shows the expected revision.

When the arXiv identifier is assigned, add Paper (PDF) and arXiv (abstract) buttons
in `index.html`, update its BibTeX, and update the paper link and citation in the
root README. Keep the project-page URL in the repository About website field.

## Layout attribution

The layout follows [GUI-Lens](https://wangshirong123.github.io/GUI-Lens/) and
[Nerfies](https://github.com/nerfies/nerfies.github.io). The adapted page layout
(`index.html` and `site.css`) is made available under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/), with attribution
retained in the page footer. SoMem code remains under the repository's MIT license;
the layout attribution does not change the licensing of paper content or figures.
