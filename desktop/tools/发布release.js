#!/usr/bin/env node
/**
 * 把打好的安装包发到 GitHub Release。
 *
 * ═══ 为什么要有这个脚本 ═══
 *
 * `release/` 是 gitignore 的（100MB 的二进制不该进 git 历史），所以 exe **不会**
 * 随分支走。组长想双击运行，就得有个地方能下载 —— 那就是 Release。
 * 但建 Release + 传附件只能走 GitHub API，本机没装 `gh`，所以用 Node 直接调。
 *
 * ═══ 用法 ═══
 *
 *     node tools/发布release.js                 # 用 package.json 的版本号
 *     node tools/发布release.js --干跑           # 只检查，不建任何东西
 *     node tools/发布release.js --版本 0.3.0    # 覆盖版本号
 *
 * 前置：先跑 `npm run dist` 生成 `release/*.exe`。
 *
 * ═══ 两个必须知道的环境坑（都踩过）═══
 *
 * 1. **必须带 `--use-system-ca`**。Node 的 TLS 配置在**进程启动时**就定死了，
 *    脚本内部改不了 —— 所以这个 flag 只能从外层给：
 *        NODE_OPTIONS=--use-system-ca node tools/发布release.js
 *    不加会报 `unable to verify the first certificate`。这是本机证书链的问题
 *    （electron-builder 打包时也是同一个坑），**不是工程问题**。
 *    ⚠️ 别用 `curl -k` 绕 —— 那等于在无法验证对方身份的信道上递 token。
 *
 * 2. **附件名不能带中文**。GitHub 的 `?name=` 参数对非 ASCII 处理不可靠：
 *    实测 `DeepProf桌宠-免安装版-0.2.0.exe` 和 `...安装版...` 会被压成**同一个**
 *    乱码名 `DeepProf.-.-0.2.0.exe`，第二个上传直接 422 already_exists。
 *    所以本脚本统一把中文名映射成 ASCII 再传（界面仍是中文，只有文件名是英文）。
 */
const { execSync } = require('child_process')
const fs = require('fs')
const path = require('path')

const REPO = 'RockeyRoc/DeepProf'
const ROOT = path.resolve(__dirname, '..')          // desktop/
const REPO_ROOT = path.resolve(ROOT, '..')          // 仓库根
const REL_DIR = path.join(ROOT, 'release')
const BRANCH = 'feat/desktop-module'                // ⚠️ 永远不往 main 上打 tag

// 中文文件名 → 上传后的 ASCII 名（理由见文件头「坑 2」）
const ASCII = [
  [/免安装版|portable/i, 'portable'],
  [/安装版|setup|nsis/i, 'setup']
]

const argv = process.argv.slice(2)
const DRY = argv.includes('--干跑')
const verArg = argv.indexOf('--版本')

function version() {
  if (verArg >= 0 && argv[verArg + 1]) return argv[verArg + 1]
  return JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8')).version
}

function token() {
  const out = execSync('git credential fill', {
    input: 'protocol=https\nhost=github.com\n\n', cwd: REPO_ROOT, encoding: 'utf8'
  })
  const m = /^password=(.*)$/m.exec(out)
  if (!m) throw new Error('取不到 GitHub 凭据（git credential fill 失败）')
  return m[1].trim()
}

/** 中文名 → `DeepProf-Pet-<kind>-<ver>.exe`；认不出来就原样返回（至少能传上去） */
function asciiName(file, ver) {
  for (const [re, kind] of ASCII) {
    if (re.test(file)) return `DeepProf-Pet-${kind}-${ver}.exe`
  }
  return file.replace(/[^\x20-\x7e]/g, '')
}

;(async () => {
  const V = version()
  const TAG = `desktop-v${V}`
  console.log(`版本 ${V}   tag ${TAG}   → ${BRANCH}${DRY ? '   【干跑模式，不建任何东西】' : ''}\n`)

  // ⚠️ 只传【当前版本】的 exe。release/ 里往往还躺着历史版本（比如 0.1.0 是
  //    上一个角色的形象），一起传上去组长会拿错 —— 这个坑本脚本替我们踩过了。
  const all = fs.existsSync(REL_DIR)
    ? fs.readdirSync(REL_DIR).filter(f => f.toLowerCase().endsWith('.exe'))
    : []
  const exes = all.filter(f => f.includes(V))
  const stale = all.filter(f => !f.includes(V))
  if (stale.length) console.log(`（跳过 ${stale.length} 个非本版本的 exe：${stale.join(', ')}）\n`)
  if (!exes.length) { console.log(`❌ release/ 下没有 v${V} 的 exe —— 先跑 npm run dist`); process.exit(1) }
  for (const f of exes) {
    console.log(`  ${f}  ${(fs.statSync(path.join(REL_DIR, f)).size / 1048576).toFixed(1)} MB  →  ${asciiName(f, V)}`)
  }
  if (DRY) { console.log('\n干跑结束。'); return }

  const tok = token()
  const H = { Authorization: 'Bearer ' + tok, 'User-Agent': 'deepprof-release' }
  const A = { ...H, Accept: 'application/vnd.github+json' }

  // tag 撞车就停 —— 不覆盖任何已有东西
  const tags = execSync('git ls-remote --tags origin', { cwd: REPO_ROOT, encoding: 'utf8' })
  if (tags.includes('refs/tags/' + TAG)) { console.log(`\n❌ tag ${TAG} 已存在于远端 —— 中止`); process.exit(2) }

  const r = await fetch(`https://api.github.com/repos/${REPO}/releases`, {
    method: 'POST',
    headers: { ...A, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      tag_name: TAG, target_commitish: BRANCH,
      name: `桌宠模块 v${V}`, draft: false, prerelease: false,
      body: `桌宠前端模块 **v${V}**。\n\n附件名为纯英文是**故意的** —— GitHub 的附件名参数对中文处理不可靠。\n\n源码分支 \`${BRANCH}\`（tag 打在这个分支上，**main 未动**）。`
    })
  })
  const rel = await r.json()
  if (r.status !== 201) { console.log('❌ 建 release 失败:', r.status, JSON.stringify(rel).slice(0, 300)); process.exit(3) }
  console.log('\n✅ release:', rel.html_url)

  for (const f of exes) {
    const p = path.join(REL_DIR, f)
    const name = asciiName(f, V)
    process.stdout.write(`上传 ${name} ... `)
    const up = await fetch(
      `https://uploads.github.com/repos/${REPO}/releases/${rel.id}/assets?name=${encodeURIComponent(name)}`,
      { method: 'POST', headers: { ...H, 'Content-Type': 'application/octet-stream' }, body: fs.readFileSync(p) }
    )
    const j = await up.json()
    console.log(up.status === 201 ? `✅ ${(j.size / 1048576).toFixed(1)} MB` : `❌ ${up.status} ${JSON.stringify(j).slice(0, 200)}`)
  }

  // 复核：落地名对不对（中文被吃掉那次就是靠这一步发现的）
  const list = await (await fetch(`https://api.github.com/repos/${REPO}/releases/${rel.id}/assets`, { headers: A })).json()
  console.log('\n附件清单（**核对名字**）：')
  for (const a of list) console.log(`  ${a.name}  ${(a.size / 1048576).toFixed(1)} MB  ${a.state}`)
})().catch(e => { console.log('出错:', e.message); process.exit(1) })
