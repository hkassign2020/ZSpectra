# Z-SPECTRA: Project, Demo, and Submission Guide

## Event assumption

This guide assumes the project is for the **IBM Z Datathon 2026**. The repository README identifies the submission as `SSF2026`, and the supplied judging image names Shooting Stars Foundation and IBM judges. Confirm the event and the latest submission requirements in your Hack2Skill account before submitting.

## What the project does

Z-SPECTRA is a synthetic cross-bank fraud-detection demonstrator. Each bank service processes its own raw synthetic events and turns them into pseudonymous IDs and limited behavioral features. A shared mesh links those features, scores possible transfer chains, and issues an alert with a hold recommendation for human review. An optional LLM creates a short explanation, which appears in the dashboard along with the chain and audit information.

This is a prototype. It does not connect to real bank accounts, real customer data, CICS, Db2, or other live bank systems. It does not automatically freeze accounts. Rollback telemetry and transaction scenarios are simulated. BIDs are pseudonymous and linkable; they are not anonymous.

Useful project files:

- `README.md` — setup and operation instructions.
- `SPEC.md` — architecture, data contracts, privacy boundaries, and evaluation requirements.
- `mesh/llm.py` — LLM request, response validation, and safe template fallback.
- `mesh/service.py` — alert creation and LLM integration.
- `run.sh` — LinuxONE setup, demo, evaluation, status, and stop commands.
- `tools/platform_proof.py` — captures host and platform evidence.
- `bench/bench.py` — benchmark and evaluation runner.
- `tests/` — automated tests.

## What is sent to the Live LLM

The mesh sends a system instruction and a compact JSON user payload. The payload contains:

- Chain entries: bank label, pseudonymous BID, time gap, and amount bin.
- The supplied `p_cascade` and `anom` model scores.
- Evidence feature names and percentile values.

The prompt does not contain raw account IDs, names, national IDs, or transaction memos. The API key is sent as an authorization credential; never put it in the source code, dashboard, video, or submission ZIP.

The LLM is asked to return five string fields: `summary`, `suspected_pattern`, `risk_level`, `recommended_action`, and `limits`. The app validates the result and otherwise uses a labeled template. The dashboard can show the prompt and the brief. Alert state is held in the mesh process; audit and benchmark output can also be written to files. The API provider receives the prompt and handles it under its own service terms and retention settings.

The project calls a chat-completions endpoint; it does not retrieve outside facts or fetch customer records from the LLM. In the current demo configuration, Gemini runs as a hosted API. The application and its ML services run on LinuxONE; the LLM inference itself is not running on LinuxONE.

## IBM Z, LinuxONE, and L1CC

IBM Z is IBM's mainframe platform family. IBM LinuxONE is a Linux-focused platform based on IBM Z architecture. Linux on these systems uses the `s390x` architecture. IBM's documentation covers [Linux on IBM Z and LinuxONE](https://www.ibm.com/docs/en/linux-on-systems?topic=linux-z-linuxone).

**L1CC** means **LinuxONE Community Cloud**. It provides Linux virtual machines on IBM LinuxONE. This project was deployed on an L1CC VM and has platform evidence for `s390x`; that supports a LinuxONE compatibility claim for this demo. It does not prove a connection to z/OS or a bank mainframe application. Do not claim Telum acceleration or DFLTCC hardware acceleration unless separate evidence and measurements verify them.

See the [IBM Z Datathon event page](https://www.starhacks.org/ibm-z-datathon-2026) and [IBM Datathon information](https://community.ibm.com/zsystems/datathon/).

## How to run the demo

Run this on the LinuxONE VM from the project directory. It starts the services and launches the scripted demo heist.

```sh
cd ~/ZSpectra
export CONSORTIUM_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
read -rsp 'Gemini API key: ' LLM_API_KEY; echo
export LLM_API_KEY
export LLM_URL='https://generativelanguage.googleapis.com/v1beta/openai/chat/completions'
export LLM_MODEL='gemini-3.7-flash'
export LLM_REASONING_EFFORT='low'
./run.sh demo
```

Open `http://127.0.0.1:8080` on the VM. From another computer, use an SSH tunnel to reach the VM's loopback-bound dashboard port. The dashboard's **LLM BRIEF** should show `API` when the configured hosted model returns a valid response; `template` indicates the call failed or returned an invalid response.

Other useful commands from the project directory:

```sh
./run.sh status
./run.sh eval
./run.sh stop
python3 tools/platform_proof.py
```

`./run.sh eval` generates `results.json` and updates the README results summary. Evaluation uses synthetic data; report the actual generated measurements and their limitations. The current local README says measured results are not attached to this checkout, so include the target-host evaluation output if it is available before presenting benchmark claims.

## Available tests

Run the suite from the repository root with:

```sh
python3 -m pytest
```

Test modules cover:

- Core schema and behavior: `test_schema`, `test_generator`, `test_sensor`, `test_heist`, `test_chain`, `test_mesh_features`, `test_models`, `test_train`, and `test_walking_skeleton`.
- Privacy and LLM boundaries: `test_audit`, `test_audit_logging`, `test_canary_index`, and `test_llm`.
- Services and runtime: `test_bank_service`, `test_mesh_service`, `test_replay_service`, `test_dashboard`, `test_process_runner`, and `test_http_session_pool`.
- Packaging and platform: `test_packaging`, `test_prepare_demo`, and `test_platform_proof`.
- Evaluation: `test_bench` and `test_zlib_bench`.

The previous full test run for this project passed **101 tests**.

## Judging rubric from the supplied image

The image gives a maximum of **40 points**, with four categories worth 10 points each. Each category uses the following bands: **Developing: 0–3**, **Proficient: 4–6**, **Advanced: 7–8**, and **Exceptional: 9–10**.

| Category | Project evidence | What to strengthen |
|---|---|---|
| Community Value & Theme Alignment | Cross-bank fraud prevention and limiting shared data align with real-time AI and protected-data themes. | Explain who benefits. Be clear that synthetic data does not establish real-world effectiveness. |
| Novelty & Originality | The bank-local processing, blind mesh, boundary audit, and LLM explanation form a coherent architecture. | Identify what the team created, distinguish it from libraries/services, and credit open-source components. |
| Technical Design & LinuxONE/L1CC Use | The project runs in containers on LinuxONE and has `s390x` platform evidence. | Clearly distinguish hosted Gemini from LinuxONE inference. Do not claim Telum or DFLTCC acceleration without proof. Include reproducible measurements and describe security limitations. |
| Execution, Demo & Video Pitch | The dashboard runs a synthetic heist and displays the alert and LLM mode. | Record a concise, polished walkthrough on the live LinuxONE VM and include results or clearly label any missing measurements. |

This is a preparation guide, not an official score. Judges determine the actual rating.

## What “live proof” and the video likely refer to

The phrase “a video like that” is unclear from the image alone. The rubric asks for a professional video pitch with a working live LinuxONE demo. The linked Datathon rules are summarized as requiring a demo video of no more than three minutes; check the [official rules linked by the organizer](https://drive.google.com/file/d/1nYhDRZTii05hFRaSbKutI_LH0THsxGIc/view) and your Hack2Skill submission form for the exact requirements.

A useful screen recording can show:

1. The project name and the problem it addresses.
2. The live dashboard and the scripted heist.
3. The detected chain, evidence, and human-review hold recommendation.
4. The `API` label beside the LLM brief.
5. LinuxONE platform proof, such as `s390x` architecture and healthy services.
6. The boundary-audit result and a brief explanation of synthetic data and privacy limits.

Keep API keys, passwords, private-key files, and personal information off-screen. Describe DFLTCC or hardware acceleration only if measured and supported by the captured evidence.

## How to submit and receive a rating

The IBM Z Datathon event page points participants to Hack2Skill. Log in to your Hack2Skill account, open the IBM Z Datathon 2026 event, and use its project-submission flow. Prepare the project description, pitch deck, source repository link or ZIP, and demo video. Follow the title format and file/link fields shown in the current form. The [event rules summary](https://viamu.app/opportunities/ibm-z-datathon) says the final submission includes a reviewer-accessible repository or ZIP and a demo video of up to three minutes; use the organizer-linked rules and live form as the authority.

Before submitting:

1. Check the submission page immediately for its deadline and required fields. The event runs October 17–18, 2026, and the organizer's event materials give October 18 as the final day; the exact cutoff time should be confirmed in Hack2Skill.
2. Make sure the linked repository contains the version you intend judges to review. This workspace has uncommitted local changes that are not on GitHub. Because you previously requested no direct push, those edits have not been pushed. A ZIP can include the current working files; otherwise, the GitHub link will show the older committed version.
3. Do not ZIP the whole workspace. Exclude `.venv`, `.git`, caches, logs, API keys, private keys, and unrelated coursework documents. Include project code, tests, Docker files, README/SPEC, and any measured results you want reviewed.
4. Submit, then confirm Hack2Skill shows the submission as received. Keep a screenshot or confirmation email.

Judges assign the rating; uploading does not generate a score immediately. Check Hack2Skill and organizer messages for results or feedback. The repo shows work before the event, so confirm with the organizer that using this existing project is eligible: the linked rules summary says the solution must be built during the Datathon window and restricts reuse of a substantially completed earlier project. Be accurate about which work was done during the event.
