# Avocat AI Moldova

A local AI legal information assistant for the laws of the Republic of Moldova, with a Romanian interface. Ask a question in plain language and get an answer with article references and links to the sources. The model, index and chat history all stay on your computer.

[Citește în română](README.ro.md)

> [!WARNING]
> **This is not legal advice and it is not a lawyer.** Answers are generated automatically and can be wrong, incomplete or out of date. Always check the cited source, and consult a licensed lawyer for important decisions.

## Features

- **Chat with sources:** answers cite specific articles, with links to the act in the app and on the official sites.
- **Search:** full-text and title search across indexed acts, with filters for type, status, domain and year. It handles missing diacritics and references like `95/2021`.
- **Tree routing:** each question goes to a legal branch first, so a customs question only searches customs acts. The route is shown as it happens.
- **Status tracking:** adoption and publication dates, detected repeals, the replacing act, and known future changes.
- **Model management:** download, switch, delete or add any Ollama model or Hugging Face GGUF from the UI. Load or unload a model from memory, or stop the AI engine entirely to free all of it.
- **Background indexing:** resumes after restarts and only fetches new issues. Failed issues retry automatically or from the Probleme page.

## Quick start

Running the app requires written permission from the copyright holder (see [License](#license)).

**Requirements:** Windows 10 or 11 (x64) and an internet connection for the first run.

With permission, download this repository and double-click `run.bat`. Nothing is installed system-wide: uv, Python, Ollama and the models all stay inside the project folder.

The first run downloads the components below, and the browser opens at <http://127.0.0.1:18917>. If that port is taken, the app uses the next free one.

To use the app from other devices on your LAN or VPN, start it with `run-lan.bat` instead and allow port 18917 in the firewall. The console lists the addresses to open, such as `http://192.168.1.20:18917`. There is no login, so anyone who can reach that port can use the app and read its conversations: do this only on networks you trust.

### What it downloads

| Component | Size |
| --- | --- |
| Ollama runtime | about 1.6 GB |
| Default model | 5.2 to 17.7 GB, depending on your RAM |
| Legal index (`data/lege.db`) | grows as it indexes; expect several GB |

| System RAM | Default model | Notes |
| --- | --- | --- |
| 32 GB or more | `qwen3.8:27b` (17.7 GB) | Most accurate in testing; took about 4.5 minutes per answer on a 16 GB GPU |
| 24 GB or more | `qwen3:14b` (9.3 GB) | About 1.5 minutes per answer, but made legal and arithmetic errors in testing |
| Less | `qwen3:8b` (5.2 GB) | For modest hardware; not evaluated for legal accuracy |

These timings come from a single test question on one machine (Radeon RX 9070 XT, Ryzen 9 9900X, 32 GB RAM), so treat them as a rough guide.

## Limitations

Read these before relying on any answer.

- **Coverage is not complete.** The automatic source is Monitorul Oficial, with issues going back to about 2001. Older acts, such as the 1997 Fiscal Code, may appear as titles only until you import them.
- **Texts are not consolidated by default.** Indexed text is the version as originally published. Amendments are linked as separate acts, and the app warns you when a source is unconsolidated.
- **Status is detected automatically.** "Abrogat" is derived from the wording of other acts and can be wrong. "Fără abrogare detectată" does not guarantee an act is in force.
- **Models make mistakes.** The test answer got the customs thresholds right but still contained a wrong worked example. Citations are checked to exist, but the legal reasoning is not verified.

[`legis.md`](https://www.legis.md) has the consolidated versions but blocks automated access, and this project does not circumvent that. To add a consolidated text, open the act on `legis.md`, click **Pdf**, and drag the file into the Index page. See [Data sources](docs/DATA_SOURCES.md).

## Documentation

- [Architecture](docs/ARCHITECTURE.md): how a question is answered, how indexing works, and the database schema
- [Data sources](docs/DATA_SOURCES.md): where the data comes from, its limits, and how status is derived
- [Contributing](CONTRIBUTING.md): development setup, tests and project rules

## Configuration

Optional environment variables, set before running `run.bat`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `MAL_PORT` | `18917` | First port tried for the web UI |
| `MAL_OLLAMA_PORT` | `18971` | First port tried for the bundled Ollama |
| `MAL_DATA` | `data/` | Location of the database and models |

The app always runs its own Ollama instance on its own port and model folder, so it does not use or affect other Ollama installations.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| "Modelul AI nu este gata" | The model is still downloading. Check progress on the **Modele AI** page. Search works in the meantime. |
| The AI engine won't start | Check `.runtime/ollama.log`. |
| HTTP 504 lines in the console | Usually harmless. Each download gets 5 tries, and an issue only appears on the **Probleme** page if all of them fail. |
| Answers are slow | Choose a smaller model on the **Modele AI** page, accepting lower accuracy. |
| A code shows "Doar titlu" | Its issue isn't indexed yet or isn't available. Import the PDF from `legis.md` on the Index page. |

To reset the app, close it and delete `data/` (index, chat history, models) and `.runtime/` (downloaded components).

## Privacy

Questions and answers are processed locally and are not sent to any AI service. The app connects to the internet to download legislation, models and its runtime.

## License

**View-only, not open source.** You may read the code, but you may not run, copy, modify or distribute it without written permission. See [LICENSE](LICENSE). Official legal texts remain subject to the terms of their sources.
