# Pre-analysed dataset for mentors

## What you get

One file, `ftf-data-full-fd9e296.tgz` (1.0 GB), holds our full local analysis of the challenge
sample: 10 cases, 37 items and 11 campaign groups. Unpacked next to the code, it opens in the app
exactly as we see it.

- **Results database:** cases, items, observations and the links between items.
- **Pictures:** the screenshots and video frames shown on item pages.
- **Original material:** the files each item came from, so originals open in the app and analysis
  can be re-run.
- **Model-reading results**, the pattern library and the warning lists.

Not included: face-matching data, which the app rebuilds by itself on first start, and the model
cache.

## What you need

You need Docker and access to two things we share with you. No API keys are needed.

- **Docker** with Compose v2 (Docker Desktop on Mac or Windows), with at least 8 GB of memory given
  to it.
- **About 18 GB of free disk:** about 15 GB for the app's images, plus the 1.0 GB download and
  1.1 GB once unpacked.
- **Port 8000 free**, and internet on the first start, to download the images.
- **Access to the code:** the private GitHub repository `pavellitvinko/hacknation-nask-2026`. Ask us
  to add your GitHub account if you can't open it.
- **Access to the dataset** on Google Drive, shared with your email address.

## Download and set up

Setup takes five steps; the first start then builds the app, which takes several minutes.

1. **Download** `ftf-data-full-fd9e296.tgz` from the
   [shared Google Drive folder](https://drive.google.com/drive/folders/15ZPzXXKlonNejb-YxbDXpbDtSzDRHpup?usp=sharing).
   If Drive won't let you download it, use the
   [reserve copy on MEGA](https://mega.nz/folder/jYJghYgZ#Vq_dOvSMbuj7u2qyS5eANw).
2. **Get the code** at the exact version the dataset was made with:

   ```bash
   git clone https://github.com/pavellitvinko/hacknation-nask-2026.git
   cd hacknation-nask-2026
   git checkout fd9e296
   ```

3. **Move the downloaded file** into the `hacknation-nask-2026` folder, next to
   `docker-compose.yml`.
4. **Unpack it** there. This creates the `data` folder; if a `data` folder already exists, move it
   aside first.

   ```bash
   tar -xzf ftf-data-full-fd9e296.tgz
   ```

5. **Start the app**, then open <http://localhost:8000> once the log stops scrolling:

   ```bash
   docker compose up
   ```

To stop the app, press Ctrl+C or run `docker compose down`. The data stays in the `data` folder, so
the next `docker compose up` opens the same state.

## Using it

Start with **Campaigns**: it shows which items we link into one campaign and the evidence behind
each link.

| Page | What to look at |
| --- | --- |
| Campaigns | The 11 groups, 7 of them with more than one item. Open one to see its items and why they are linked. |
| Library | All 37 items from the 10 cases. Open an item for its picture, the checks run on it and its original files. |
| How it works | How items are grouped and what each kind of group means. |

## Things to know

- **Running analysis changes the results.** Adding material, or running checks or video analysis on
  an item, recomputes every link. To return to the state we shared, stop the app, unpack the archive
  again over the `data` folder and start it again.
- **Keep it private.** The archive holds the organiser's sample material. Don't pass it on or
  upload it anywhere public, and delete it when you are done reviewing.
- **Run it with Docker only.** The database points to files inside the container (`/data/...`).
  Started without Docker, the app can't find the original files.
- **Model reading is optional.** Results we already have show without keys. To run new model
  reading, download the `.env` file from the same Google Drive or MEGA folder, put it next to
  `docker-compose.yml` and restart with `docker compose up -d`. If your browser saves it as `env`
  or `.env.txt`, rename it to `.env`.
- **Face matching** prepares itself in the background on the first start, in about 2 minutes. The
  app is usable meanwhile.

## Troubleshooting

| What you see | What to do |
| --- | --- |
| The app opens with only a few sample campaigns | The archive was unpacked in the wrong place. The `data` folder must sit next to `docker-compose.yml`; unpack again there and restart. |
| An original file shows as missing | Same cause, or the app was started without Docker. Use `docker compose up` from the repository folder. |
| "Port 8000 is already allocated" | Another program uses the port. Stop it, or change `"8000:8000"` to `"8001:8000"` in `docker-compose.yml` and open <http://localhost:8001>. |
| The build stops or a container keeps restarting | Docker has too little memory. Give it at least 8 GB in Docker Desktop's settings and run `docker compose up` again. |
| The page looks broken after an update | Hard-refresh the browser (Cmd+Shift+R or Ctrl+Shift+R). |

If something else goes wrong, send us the last lines of the `docker compose up` log.
