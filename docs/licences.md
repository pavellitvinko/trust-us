# Libraries, models, services and data sources

Licences below come from the metadata of the versions pinned in `uv.lock`. Check each provider's
current terms and limits before use.

## Direct Python dependencies

| Library | Version | Licence |
| --- | --- | --- |
| pydantic | 2.14.4 | MIT |
| tldextract | 5.4.0 | BSD-3-Clause |
| streamlit | 1.67.0 | Apache-2.0 |
| google-genai | 3.6.1 | Apache-2.0 |
| openai | 3.57.0 | Apache-2.0 |
| pillow | 13.0.0 | MIT-CMU |
| imagehash | 4.3.2 | BSD-2-Clause |
| numpy | 2.5.5 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| scikit-learn | 1.9.1 | BSD-3-Clause |
| dnspython | 2.9.2 | ISC |
| playwright | 1.65.2 | Apache-2.0 |
| fastapi | 0.165.0 | MIT |
| python-multipart | 0.0.34 | Apache-2.0 |

`ftf serve` runs FastAPI on uvicorn 0.54.0 (BSD-3-Clause), already installed with Streamlit.

## Web screens (`frontend/package-lock.json`)

| Library | Version | Licence |
| --- | --- | --- |
| react, react-dom | 19.3.1 | MIT |
| react-router-dom | 7.18.6 | MIT |
| vite (build only) | 8.3.8 | MIT |
| @vitejs/plugin-react (build only) | 6.3.0 | MIT |
| typescript (build only) | 7.0.2 | Apache-2.0 |

The Docker image also installs `ffmpeg` from Ubuntu packages and uses the Playwright base image
with Chromium. Their licences depend on the build and should be recorded from the final image.

## Models and external services in this prototype

Because of the limited time of the hackathon, and so that the application is easy to start from
Docker with `docker compose up` without a GPU or large local language models, this prototype uses
the services below. The target application will run local models instead, including a local face
recognition model.

| Service | Default model setting | Used for | Terms |
| --- | --- | --- | --- |
| OpenAI API | `FTF_OPENAI_MODEL=gpt-6-luna` | reading, patterns, expectation profiles, narrative | <https://openai.com/policies/> |
| Google Gemini API | `FTF_EMBEDDING_MODEL=gemini-embedding-2`, `FTF_VIDEO_MODEL=gemini-4-hydrogen` | embeddings, video | <https://ai.google.dev/gemini-api/terms> |
| CompreFace with FaceNet | `exadel/compreface:1.2.0-facenet` | face matching against a closed reference list | see below |

Both APIs are used with the operator's own keys. Rate limits and data-use terms depend on the account.

## Face matching

| Component | Licence | Note |
| --- | --- | --- |
| CompreFace 1.2.0 | Apache-2.0 | <https://github.com/exadel-inc/CompreFace> |
| FaceNet code | MIT | <https://github.com/davidsandberg/facenet> |
| Reference photos | CC0, public domain, CC BY, CC BY-SA | Author, licence and source for each are in `src/ftf/faces/reference_people.json`. |

InsightFace-based CompreFace builds are not used: their models are for non-commercial research only.

## Public data sources

| Source | Used for | Address |
| --- | --- | --- |
| IANA RDAP bootstrap and registry RDAP servers | domain registration data | <https://data.iana.org/rdap/dns.json> |
| DNS resolvers | DNS records | system or configured resolver |
| crt.sh (Sectigo), certificate transparency logs | certificate history | <https://crt.sh/>; public CT log data, queried politely (one request per second, cached 7 days) |
| CERT Polska warning list | listed domains | <https://hole.cert.pl/domains/v2/domains.txt> |
| KNF public warnings | listed entities | <https://www.knf.gov.pl/dla_konsumenta/ostrzezenia_publiczne> |
| Ministry of Finance gambling register | prohibited gambling domains | <https://hazard.mf.gov.pl/> |

A missing list entry says nothing about legitimacy.

## Organiser material

The organiser's sample is not in this repository and must not be redistributed. `corpus/` holds
only processed results, and the repository must stay private.
