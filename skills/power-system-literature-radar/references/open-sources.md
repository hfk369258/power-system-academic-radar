# Open Sources For Power-System Literature Radar

Use these sources before relying on IEEE/CNKI API access.

## Default Pipeline

1. OpenAlex: main broad metadata source, no key required.
2. Semantic Scholar: open metadata, abstracts, citation signals, and open-access PDF links when available. API key is optional but recommended for stable rate limits.
3. Crossref: DOI and publisher metadata helper.
4. arXiv: preprints for optimization, control, AI, and energy systems.
5. RSS feeds: journal table-of-contents, SSRN category feeds, Zenodo community feeds, and publisher feeds.
6. Manual exports: IEEE/CNKI/WoS/Scopus/Engineering Village exports placed in watched folders.

## Optional RSS Targets

Add source-specific RSS URLs under the disabled `journal_rss` source in the JSON config. Good candidates include:

- journal table-of-contents feeds from Elsevier, Wiley, Springer Nature, MDPI, Frontiers, IEEE author alerts if exported as feed, and society journals;
- SSRN energy/electricity policy feeds when energy-market and policy papers matter;
- Zenodo community feeds when tracking datasets, code, or open-energy communities.

## Notes

Keep IEEE Xplore API disabled unless the user has an official API key. Keep CNKI as manual export unless the institution provides an explicit authorized interface.
