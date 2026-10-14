# Put the testing page online (AWS Lightsail)

About 20 minutes, about $10/month (2 GB Lightsail plan). Testers get an
**https** link (browsers only allow the microphone on https). Only the
testing page and the results dashboard are public.

## 1. Push the latest code to GitHub (your computer)

```powershell
cd $HOME\Desktop\string
git add -A
git commit -m "Testing page ready for AWS"
git push
```

## 2. Create the server (AWS console)

1. Open https://lightsail.aws.amazon.com and click **Create instance**.
2. **Region:** Mumbai (ap-south-1), or the one nearest your testers.
3. **Platform:** Linux/Unix. **Blueprint:** *OS Only* -> **Ubuntu 24.04 LTS**
   (not 22.04: its Python is too old).
4. **Plan:** the **2 GB** plan. Name it, e.g. `nueriq-testing`, and create it.
5. When it shows *Running*: **Networking** tab ->
   - **Create static IP** and attach it (so the address never changes);
   - under **IPv4 Firewall** click **Add rule** -> **HTTPS** (443). HTTP (80)
     is already open; both are needed.

## 3. Install the app (on the server)

Click **Connect using SSH** (opens a terminal in the browser). The repository
is private, so GitHub asks for a **token** instead of a password: on GitHub,
*Settings -> Developer settings -> Fine-grained tokens -> Generate new token*,
*Only select repositories* -> `Preetham-string`, *Repository permissions ->
Contents: Read-only*, 7-day expiry. Then:

```bash
git clone https://github.com/preeetham12/Preetham-string.git
# Username: preeetham12      Password: paste the token (nothing shows - normal)
cd Preetham-string
sudo bash deploy/setup.sh
```

It finishes by printing the **link for testers**, the **dashboard link** and
the **dashboard key**.

## Updating later

Push the new code to GitHub (step 1), then on the server:

```bash
cd ~/Preetham-string && sudo bash deploy/update.sh
```

## Good to know

- **Your own domain:** point a DNS *A record* at the static IP, then run
  `sudo bash deploy/setup.sh tests.yourdomain.com`.
- **Backups:** Lightsail -> your instance -> **Snapshots** -> *Enable automatic
  snapshots*. Results and recordings live in `/var/lib/stringcoach`.
- **Dashboard key:** `sudo grep KEY /etc/stringcoach.env`.
- **Something wrong?** `sudo journalctl -u stringcoach -n 50` (app) and
  `sudo journalctl -u caddy -n 50` (https).
- **Privacy:** recordings are of people; tell testers how long you keep them,
  and delete tests from the dashboard when asked.
