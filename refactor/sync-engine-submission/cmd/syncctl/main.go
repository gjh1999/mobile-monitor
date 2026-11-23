// Command syncctl is the process orchestrator and single entry point for
// reviewing: it starts/stops/kills the three processes, reports status,
// waits for convergence, triggers sync rounds, and forwards fault injection.
//
// Usage:
//
//	syncctl up    [--root DIR] [--fresh]   start server + A + B (one command)
//	syncctl down  [--root DIR]             stop all three
//	syncctl kill  <server|a|b> [--root D]  force-kill one process
//	syncctl start <server|a|b> [--root D]  (re)start one process on its data dir
//	syncctl status [--wait] [--timeout S]  health + queues of A/B
//	syncctl sync  <a|b|all> [--root D]     trigger one explicit sync round
//	syncctl converge [--timeout S]         sync in rounds until both queues drain
//	syncctl fault arm --mode commit-ack --target A [--count 1]
//	syncctl fault clear
//	syncctl serverlog [--limit N]          coordinator commit log (forensics)
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"
)

const (
	serverAddr = "127.0.0.1:8730"
	serverURL  = "http://127.0.0.1:8730"
	clientA    = "127.0.0.1:8731"
	clientB    = "127.0.0.1:8732"
)

type proc struct {
	name     string // "server" | "a" | "b"
	exe      string
	args     []string
	pidFile  string
	logFile  string
	health   string // URL to poll until ready
	spawnDir string
}

func main() {
	if len(os.Args) < 2 {
		usage()
	}
	cmd := os.Args[1]
	fs := flag.NewFlagSet(cmd, flag.ExitOnError)
	root := fs.String("root", "", "run root dir (default <cwd>/run)")
	fresh := fs.Bool("fresh", false, "wipe the run root first (empty-state start)")
	wait := fs.Bool("wait", false, "status: poll until queues drain or timeout")
	timeout := fs.Int("timeout", 30, "seconds for --wait / converge")
	fs.Parse(os.Args[2:])

	r := *root
	if r == "" {
		r = "run"
	}
	r, _ = filepath.Abs(r)

	var err error
	switch cmd {
	case "up":
		err = doUp(r, *fresh)
	case "down":
		err = doDown(r)
	case "kill":
		if fs.NArg() < 1 {
			err = fmt.Errorf("kill requires server|a|b")
		} else {
			err = doKill(r, fs.Arg(0))
		}
	case "start":
		if fs.NArg() < 1 {
			err = fmt.Errorf("start requires server|a|b")
		} else {
			err = doStart(r, fs.Arg(0))
		}
	case "status":
		err = doStatus(r, *wait, *timeout)
	case "sync":
		if fs.NArg() < 1 {
			err = fmt.Errorf("sync requires a|b|all")
		} else {
			err = doSync(fs.Arg(0))
		}
	case "converge":
		err = doConverge(*timeout)
	case "fault":
		err = doFault(fs.Args())
	case "serverlog":
		err = doServerLog(firstInt(fs.Args(), 50))
	default:
		usage()
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}

func usage() {
	fmt.Fprint(os.Stderr, strings.TrimPrefix(`
up [--root DIR] [--fresh] | down | kill <server|a|b> | start <server|a|b>
status [--wait] [--timeout S] | sync <a|b|all> | converge [--timeout S]
fault arm --mode commit-ack|events-ack --target A|B|any [--count N] | fault clear
serverlog [--limit N]
`, "\n"))
	os.Exit(2)
}

func firstInt(args []string, def int) int {
	for i, a := range args {
		if a == "--limit" && i+1 < len(args) {
			n, _ := strconv.Atoi(args[i+1])
			return n
		}
	}
	return def
}

// ---------- process plumbing ----------

func binDir() string {
	exe, err := os.Executable()
	if err != nil {
		return "."
	}
	return filepath.Dir(exe)
}

func procs(r string) map[string]proc {
	bin := binDir()
	return map[string]proc{
		"server": {
			name: "server", exe: filepath.Join(bin, "server.exe"),
			args:   []string{filepath.Join(r, "server-data"), serverAddr},
			pidFile: filepath.Join(r, "server.pid"), logFile: filepath.Join(r, "logs", "server.log"),
			health: serverURL + "/v1/status", spawnDir: r,
		},
		"a": {
			name: "a", exe: filepath.Join(bin, "syncclient.exe"),
			args: []string{"launch", "--id", "A", "--listen", clientA, "--server", serverURL,
				"--project", filepath.Join(r, "project-a"), "--state", filepath.Join(r, "a-state")},
			pidFile: filepath.Join(r, "a.pid"), logFile: filepath.Join(r, "logs", "a.log"),
			health: "http://" + clientA + "/status", spawnDir: r,
		},
		"b": {
			name: "b", exe: filepath.Join(bin, "syncclient.exe"),
			args: []string{"launch", "--id", "B", "--listen", clientB, "--server", serverURL,
				"--project", filepath.Join(r, "project-b"), "--state", filepath.Join(r, "b-state")},
			pidFile: filepath.Join(r, "b.pid"), logFile: filepath.Join(r, "logs", "b.log"),
			health: "http://" + clientB + "/status", spawnDir: r,
		},
	}
}

func spawn(p proc) (int, error) {
	os.MkdirAll(filepath.Dir(p.logFile), 0o755)
	logf, err := os.OpenFile(p.logFile, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return 0, err
	}
	cmd := exec.Command(p.exe, p.args...)
	cmd.Dir = p.spawnDir
	cmd.Stdout = logf
	cmd.Stderr = logf
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	if err := cmd.Start(); err != nil {
		return 0, err
	}
	pid := cmd.Process.Pid
	logf.Close()
	os.WriteFile(p.pidFile, []byte(strconv.Itoa(pid)), 0o644)
	return pid, nil
}

func waitHealthy(url string, d time.Duration) error {
	deadline := time.Now().Add(d)
	for time.Now().Before(deadline) {
		resp, err := http.Get(url)
		if err == nil {
			io.Copy(io.Discard, resp.Body)
			resp.Body.Close()
			if resp.StatusCode == 200 {
				return nil
			}
		}
		time.Sleep(150 * time.Millisecond)
	}
	return fmt.Errorf("not healthy within %s: %s", d, url)
}

func readPID(r, name string) (int, bool) {
	p := procs(r)[name]
	b, err := os.ReadFile(p.pidFile)
	if err != nil {
		return 0, false
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(b)))
	if err != nil || pid <= 0 {
		return 0, false
	}
	return pid, true
}

func killPID(pid int) error {
	p, err := os.FindProcess(pid)
	if err != nil {
		return nil // already gone
	}
	return p.Kill() // TerminateProcess on Windows: a hard kill
}

// ---------- commands ----------

func doUp(r string, fresh bool) error {
	if fresh {
		os.RemoveAll(r)
	}
	for _, d := range []string{r, filepath.Join(r, "logs")} {
		os.MkdirAll(d, 0o755)
	}
	ps := procs(r)
	// Refuse to double-start if everything is already healthy.
	if err := waitHealthy(ps["server"].health, 300*time.Millisecond); err == nil {
		return fmt.Errorf("server already running; use syncctl down first")
	}
	pid, err := spawn(ps["server"])
	if err != nil {
		return fmt.Errorf("spawn server: %w", err)
	}
	fmt.Printf("[up] server pid=%d addr=%s state=%s\n", pid, serverAddr, ps["server"].args[0])
	if err := waitHealthy(ps["server"].health, 10*time.Second); err != nil {
		return err
	}
	for _, id := range []string{"a", "b"} {
		pid, err := spawn(ps[id])
		if err != nil {
			return fmt.Errorf("spawn %s: %w", id, err)
		}
		if err := waitHealthy(ps[id].health, 10*time.Second); err != nil {
			return err
		}
		fmt.Printf("[up] client %s pid=%d listen=%s project=%s state=%s\n",
			strings.ToUpper(id), pid, ps[id].args[4], ps[id].args[8], ps[id].args[10])
	}
	fmt.Println("[up] all three processes healthy")
	return nil
}

func doDown(r string) error {
	for _, id := range []string{"a", "b", "server"} {
		if pid, ok := readPID(r, id); ok {
			killPID(pid)
			fmt.Printf("[down] killed %s pid=%d\n", id, pid)
		}
		os.Remove(procs(r)[id].pidFile)
	}
	return nil
}

func doKill(r, id string) error {
	p, ok := procs(r)[id]
	if !ok {
		return fmt.Errorf("unknown role %q (server|a|b)", id)
	}
	pid, has := readPID(r, id)
	if !has {
		return fmt.Errorf("no pid file for %s at %s", id, p.pidFile)
	}
	if err := killPID(pid); err != nil {
		return fmt.Errorf("kill %s: %w", id, err)
	}
	fmt.Printf("[kill] %s pid=%d terminated; its data dir stays for recovery:\n", id, pid)
	if id == "server" {
		fmt.Println("  " + p.args[0])
	} else {
		fmt.Printf("  project=%s state=%s\n", p.args[8], p.args[10])
	}
	return nil
}

func doStart(r, id string) error {
	p, ok := procs(r)[id]
	if !ok {
		return fmt.Errorf("unknown role %q (server|a|b)", id)
	}
	if err := waitHealthy(p.health, 300*time.Millisecond); err == nil {
		return fmt.Errorf("%s already running", id)
	}
	if id != "server" { // server-data is created by the server itself
		for _, d := range []string{p.args[8], p.args[10]} {
			os.MkdirAll(d, 0o755)
		}
	}
	pid, err := spawn(p)
	if err != nil {
		return fmt.Errorf("spawn %s: %w", id, err)
	}
	if err := waitHealthy(p.health, 10*time.Second); err != nil {
		return err
	}
	fmt.Printf("[start] %s pid=%d healthy on same data dir\n", id, pid)
	return nil
}

// ---------- status / sync ----------

func getJSON(url string, out any, d time.Duration) error {
	c := &http.Client{Timeout: d}
	resp, err := c.Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return fmt.Errorf("http %d from %s", resp.StatusCode, url)
	}
	return json.NewDecoder(resp.Body).Decode(out)
}

func postJSON(url string, body string, out any, d time.Duration) error {
	c := &http.Client{Timeout: d}
	resp, err := c.Post(url, "application/json", strings.NewReader(body))
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 200 {
		return fmt.Errorf("http %d from %s: %s", resp.StatusCode, url, string(b))
	}
	if out == nil {
		return nil
	}
	return json.Unmarshal(b, out)
}

type clientStatus struct {
	ClientID  string `json:"client_id"`
	Online    bool   `json:"online"`
	Version   int64  `json:"version"`
	Pending   int    `json:"pending_changes"`
	Conflicts int    `json:"conflicts"`
	Events    int    `json:"events"`
	Applied   int64  `json:"applied_seq"`
}

func fetchClientStatus(url string) (*clientStatus, error) {
	var cs clientStatus
	if err := getJSON(url+"/status", &cs, 5*time.Second); err != nil {
		return nil, err
	}
	return &cs, nil
}

func doStatus(r string, wait bool, timeout int) error {
	printAll := func() (converged bool) {
		converged = true
		var srv map[string]any
		if err := getJSON(serverURL+"/v1/status", &srv, 2*time.Second); err != nil {
			fmt.Println("server: DOWN")
			converged = false
		} else {
			fmt.Printf("server: version=%v commits=%v dup_hits=%v events=%v fault_armed=%v\n",
				srv["version"], srv["commit_count"], srv["dup_hits"], srv["event_count"], faultArmed(srv))
		}
		for _, id := range []string{"a", "b"} {
			cs, err := fetchClientStatus("http://" + map[string]string{"a": clientA, "b": clientB}[id])
			if err != nil {
				fmt.Printf("client %s: DOWN\n", strings.ToUpper(id))
				converged = false
				continue
			}
			fmt.Printf("client %s: online=%v pending_changes=%d conflicts=%d events=%d version=%d\n",
				strings.ToUpper(id), cs.Online, cs.Pending, cs.Conflicts, cs.Events, cs.Version)
			if cs.Pending > 0 || !cs.Online {
				converged = false
			}
		}
		return converged
	}
	if !wait {
		printAll()
		return nil
	}
	deadline := time.Now().Add(time.Duration(timeout) * time.Second)
	for {
		if printAll() {
			fmt.Println("[status] converged")
			return nil
		}
		if time.Now().After(deadline) {
			return fmt.Errorf("not converged within %ds (see pending/conflicts above)", timeout)
		}
		time.Sleep(500 * time.Millisecond)
	}
}

func faultArmed(srv map[string]any) bool {
	f, ok := srv["fault"].(map[string]any)
	if !ok {
		return false
	}
	armed, _ := f["armed"].(bool)
	return armed
}

func doSync(who string) error {
	targets := []string{}
	switch who {
	case "a":
		targets = []string{"A"}
	case "b":
		targets = []string{"B"}
	case "all":
		targets = []string{"A", "B"}
	default:
		return fmt.Errorf("sync requires a|b|all")
	}
	for _, id := range targets {
		url := clientA
		if id == "B" {
			url = clientB
		}
		var rep map[string]any
		if err := postJSON(url+"/sync", "{}", &rep, 25*time.Second); err != nil {
			return fmt.Errorf("sync %s: %w", id, err)
		}
		b, _ := json.Marshal(rep)
		fmt.Printf("[sync %s] %s\n", id, string(b))
	}
	return nil
}

// doConverge triggers sync rounds on both clients until neither pushes nor
// pulls anything in a round, or until the timeout. It reports remaining
// pending work and conflicts explicitly when it gives up.
func doConverge(timeout int) error {
	deadline := time.Now().Add(time.Duration(timeout) * time.Second)
	round := 0
	for {
		round++
		busy := false
		for _, id := range []string{"A", "B"} {
			url := clientA
			if id == "B" {
				url = clientB
			}
			var rep map[string]any
			if err := postJSON(url+"/sync", "{}", &rep, 25*time.Second); err != nil {
				return fmt.Errorf("converge round %d sync %s: %w", round, id, err)
			}
			pushed, _ := rep["pushed"].(float64)
			pulled, _ := rep["pulled"].(float64)
			confl, _ := rep["conflicts"].(float64)
			pend, _ := rep["pending_push"].(float64)
			if pushed > 0 || pulled > 0 || confl > 0 || pend > 0 {
				busy = true
			}
			b, _ := json.Marshal(rep)
			fmt.Printf("[converge r%d %s] %s\n", round, id, string(b))
		}
		if !busy {
			fmt.Printf("[converge] converged after %d round(s)\n", round)
			return nil
		}
		if time.Now().After(deadline) {
			// Explicit report of what is still stuck, as the task requires.
			for _, id := range []string{"A", "B"} {
				url := clientA
				if id == "B" {
					url = clientB
				}
				if cs, err := fetchClientStatus(url); err == nil {
					fmt.Printf("[converge] STILL PENDING on %s: changes=%d conflicts=%d\n",
						id, cs.Pending, cs.Conflicts)
				}
			}
			return fmt.Errorf("not converged within %ds", timeout)
		}
		time.Sleep(200 * time.Millisecond)
	}
}

// ---------- fault / forensics ----------

func doFault(args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("fault requires arm|clear")
	}
	switch args[0] {
	case "clear":
		return postJSON(serverURL+"/v1/admin/fault", `{"armed":false}`, nil, 5*time.Second)
	case "arm":
		mode := "commit-ack"
		target := "any"
		count := 1
		for i := 1; i < len(args); i++ {
			switch args[i] {
			case "--mode":
				i++
				mode = args[i]
			case "--target":
				i++
				target = args[i]
			case "--count":
				i++
				fmt.Sscanf(args[i], "%d", &count)
			}
		}
		body := fmt.Sprintf(`{"armed":true,"mode":%q,"target":%q,"count":%d}`, mode, target, count)
		var out map[string]any
		if err := postJSON(serverURL+"/v1/admin/fault", body, &out, 5*time.Second); err != nil {
			return err
		}
		b, _ := json.Marshal(out)
		fmt.Printf("[fault] %s\n", string(b))
		return nil
	default:
		return fmt.Errorf("fault requires arm|clear")
	}
}

func doServerLog(limit int) error {
	var out map[string]any
	if err := getJSON(fmt.Sprintf("%s/v1/log?limit=%d", serverURL, limit), &out, 5*time.Second); err != nil {
		return err
	}
	b, _ := json.MarshalIndent(out, "", "  ")
	fmt.Println(string(b))
	return nil
}
