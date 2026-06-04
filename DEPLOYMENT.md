# Deployment Guide: Pushing to GitHub & Publishing on cPanel

This repository is pre-configured and ready for production hosting. It includes:
- A `.gitignore` file that excludes local development database files (`db.sqlite3`), virtual environments, and python caches.
- Environment variables in `settings.py` for loading secure credentials (Secret Key, Allowed Hosts, Debug state) dynamically.
- `passenger_wsgi.py` in the root directory (required by cPanel Passenger to execute the WSGI application).
- Static assets path configurations (`STATIC_ROOT`) to gather CSS, JS, and icons for production serving.

---

## Step 1: Push Project to GitHub

Run these commands inside your local project terminal to initialize git and push the project to your GitHub repository:

1. **Check Git Status**:
   ```bash
   git status
   ```
2. **Stage and Commit all files**:
   ```bash
   git add .
   git commit -m "Configure production deployment files for cPanel"
   ```
3. **Create a new repository on GitHub** (do not initialize with README/gitignore as we already have them).
4. **Link and Push**:
   ```bash
   git branch -M main
   git remote add origin https://github.com/your-username/your-repo-name.git
   git push -u origin main
   ```

---

## Step 2: Deploy to cPanel (via Git Version Control)

1. Log into your cPanel account.
2. Search for **Git Version Control** and click it.
3. Click **Create** to register a repository:
   - **Clone URL**: Enter your GitHub repo URL (e.g. `https://github.com/your-username/your-repo-name.git`).
   - **Repository Path**: Enter a folder name inside your home directory (e.g. `public_html/starlink-manager` or a subdirectory, but ideally place it outside `public_html` for security, e.g. `~/starlink_manager`).
   - **Display Name**: Starlink Manager
4. Click **Create**. cPanel will clone the repository.

---

## Step 3: Configure Python App in cPanel

1. In cPanel, search for **Setup Python App** (uses Phusion Passenger).
2. Click **Create Application**:
   - **Python Version**: Select `3.10` or `3.11`.
   - **Application root**: Path to your cloned folder (e.g. `starlink_manager`).
   - **Application URL**: Your domain name (or subdomain) where you want to access the app (e.g. `starlink.yourdomain.com`).
   - **Application startup file**: Enter `passenger_wsgi.py`.
   - **Application Entry point**: Leave blank (default is `application`).
3. Under **Configuration files**:
   - Enter `requirements.txt` and click **Add**.
4. Scroll down to **Environment variables** and add these required variables:
   - **Name**: `DJANGO_SECRET_KEY`  |  **Value**: *[Enter a long random secure string]*
   - **Name**: `DJANGO_DEBUG`       |  **Value**: `False`
   - **Name**: `DJANGO_ALLOWED_HOSTS` |  **Value**: `starlink.yourdomain.com,yourdomain.com` (your exact domain names separated by commas)
5. Click **Create** at the top right.

---

## Step 4: Install Modules, Run Migrations, & Collect Static Files

Once the application is created, cPanel will provide a command at the top to enter the virtual environment, e.g.:
`source /home/username/nodevenv/starlink_manager/3.10/bin/activate && cd /home/username/starlink_manager`

1. Open the cPanel **Terminal** tool (or SSH into your account) and paste that command to enter the environment.
2. **Install Packages**:
   ```bash
   requirements.txt
   ```
3. **Execute database migrations**:
   ```bash
   manage.py migrate
   ```
4. **Collect Static Assets**:
   ```bash
   manage.py collectstatic --noinput
   ```
5. **Create your admin user**:
   ```bash
   manage.py createsuperuser  --username admin --email "you@domain.com" --noinput
   ```

---

## Step 5: Start/Restart the Application

1. Go back to **Setup Python App** in cPanel.
2. Click the **Restart** button on your app card.
3. Access your domain (e.g. `starlink.yourdomain.com`) in your browser to verify it is live!