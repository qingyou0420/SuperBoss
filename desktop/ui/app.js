const headline = document.getElementById("headline");
const facts = document.getElementById("facts");
const homeInput = document.getElementById("home-input");
const saveHome = document.getElementById("save-home");
const startBtn = document.getElementById("start");
const openBtn = document.getElementById("open");
const updateStackBtn = document.getElementById("update-stack");
const stopBtn = document.getElementById("stop");
const writeHostsBtn = document.getElementById("write-hosts");
const checkUpdateBtn = document.getElementById("check-update");
const installUpdateBtn = document.getElementById("install-update");
const updateNote = document.getElementById("update-note");
const errorEl = document.getElementById("error");
const warningsEl = document.getElementById("warnings");
const logEl = document.getElementById("log");

function core() {
    const invoke = window.__TAURI__?.core?.invoke;
    if (!invoke) {
        throw new Error("请从 SuperBoss 桌面客户端打开，而不是浏览器。");
    }
    return invoke;
}

function esc(value) {
    return String(value).replace(/[&<>"']/g, (ch) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
    })[ch]);
}

function mark(ok) {
    return ok ? '<span class="ok">就绪</span>' : '<span class="warn">未就绪</span>';
}

function setBusy(busy) {
    for (const button of [
        startBtn,
        openBtn,
        updateStackBtn,
        stopBtn,
        saveHome,
        checkUpdateBtn,
        installUpdateBtn,
        writeHostsBtn,
    ]) {
        button.disabled = busy;
    }
    if (!busy) {
        openBtn.disabled = !openBtn.dataset.live;
    }
}

function showError(message) {
    if (!message) {
        errorEl.hidden = true;
        errorEl.textContent = "";
        return;
    }
    errorEl.hidden = false;
    errorEl.textContent = message;
}

function renderStatus(status) {
    const live = Boolean(status.live);
    openBtn.dataset.live = live ? "1" : "";
    openBtn.disabled = !live;
    writeHostsBtn.hidden = Boolean(status.hostsOk);
    headline.textContent = live ? "栈已就绪，可以打开工作台。" : "本机栈未就绪。";
    homeInput.value = status.home || "";
    facts.innerHTML = [
        ["仓库", esc(status.home || "未设置")],
        ["Docker", mark(status.docker)],
        ["编排文件", mark(status.compose)],
        [".env", mark(status.envFile)],
        ["证书", mark(status.tls)],
        ["hosts", mark(status.hostsOk)],
        ["接口", mark(status.live)],
    ]
        .map(([key, value]) => `<dt>${key}</dt><dd>${value}</dd>`)
        .join("");
    warningsEl.replaceChildren(
        ...(status.warnings || []).map((text) => {
            const item = document.createElement("li");
            item.textContent = text;
            return item;
        }),
    );
    showError(status.error || "");
    if (status.log && status.log.trim()) {
        logEl.hidden = false;
        logEl.textContent = status.log.trim();
    }
}

async function invokeStatus(name, payload) {
    setBusy(true);
    showError("");
    try {
        const status = await core()(name, payload);
        renderStatus(status);
        return status;
    } catch (err) {
        showError(String(err));
        throw err;
    } finally {
        setBusy(false);
    }
}

saveHome.addEventListener("click", () => {
    invokeStatus("set_home", { path: homeInput.value }).catch(() => {});
});

startBtn.addEventListener("click", () => {
    headline.textContent = "正在启动本机栈…";
    invokeStatus("start_stack").catch(() => {});
});

updateStackBtn.addEventListener("click", () => {
    headline.textContent = "正在 git pull 并重建镜像…";
    invokeStatus("update_stack").catch(() => {});
});

stopBtn.addEventListener("click", () => {
    headline.textContent = "正在停止本机栈…";
    invokeStatus("stop_stack").catch(() => {});
});

writeHostsBtn.addEventListener("click", () => {
    headline.textContent = "请在 UAC 窗口同意写入 hosts…";
    invokeStatus("write_hosts").catch(() => {});
});

openBtn.addEventListener("click", () => {
    window.location.replace("https://app.localhost/login");
});

checkUpdateBtn.addEventListener("click", async () => {
    setBusy(true);
    updateNote.textContent = "正在检查…";
    try {
        const info = await core()("check_update");
        if (info.available) {
            updateNote.textContent = `有新版本 ${info.version}（当前 ${info.currentVersion}）。`;
            installUpdateBtn.hidden = false;
        } else {
            updateNote.textContent = `已是最新（${info.currentVersion}）。私有仓库若未公开 latest.json，检查会显示无更新。`;
            installUpdateBtn.hidden = true;
        }
    } catch (err) {
        updateNote.textContent = "";
        showError(String(err));
    } finally {
        setBusy(false);
    }
});

installUpdateBtn.addEventListener("click", async () => {
    setBusy(true);
    updateNote.textContent = "正在下载并安装…";
    try {
        await core()("install_update");
    } catch (err) {
        showError(String(err));
        setBusy(false);
    }
});

async function boot() {
    try {
        const status = await invokeStatus("stack_status");
        if (status.live) {
            headline.textContent = "栈已就绪。可打开工作台，或先检查客户端更新。";
        }
    } catch {
        headline.textContent = "无法检测本机栈。";
    }
}

boot();
