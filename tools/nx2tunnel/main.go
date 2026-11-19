// nx2tunnel: gives Eden's Claude bridge (127.0.0.1) a public https address through ngrok, so a
// Claude custom connector can reach it. Built for android/arm64 and shipped as libnx2tunnel.so
// (Android only executes files from the app's native library folder).
//
// Usage: NGROK_AUTHTOKEN=... libnx2tunnel.so http://127.0.0.1:38617 [https://fixed.ngrok-free.app]
// Prints "URL <public url>" once online, "ERR <message>" on failure (then exits non-zero).
package main

import (
	"context"
	"fmt"
	"net"
	"os"
	"time"

	"golang.ngrok.com/ngrok/v2"
)

func init() {
	// Android has no /etc/resolv.conf, and Go's own resolver would then ask 127.0.0.1:53.
	if _, err := os.Stat("/etc/resolv.conf"); err != nil {
		net.DefaultResolver = &net.Resolver{
			PreferGo: true,
			Dial: func(ctx context.Context, network, _ string) (net.Conn, error) {
				dialer := net.Dialer{Timeout: 5 * time.Second}
				var last error
				for _, server := range []string{"1.1.1.1:53", "8.8.8.8:53", "9.9.9.9:53"} {
					conn, err := dialer.DialContext(ctx, network, server)
					if err == nil {
						return conn, nil
					}
					last = err
				}
				return nil, last
			},
		}
	}
}

func fail(format string, args ...any) {
	fmt.Printf("ERR "+format+"\n", args...)
	os.Exit(1)
}

func main() {
	if len(os.Args) < 2 {
		fail("usage: nx2tunnel <upstream> [url]")
	}
	token := os.Getenv("NGROK_AUTHTOKEN")
	if token == "" {
		fail("no ngrok authtoken")
	}
	// Exit with the app: if Eden's process dies, this one is re-parented.
	parent := os.Getppid()
	go func() {
		for range time.Tick(time.Second) {
			if os.Getppid() != parent {
				os.Exit(0)
			}
		}
	}()
	ctx := context.Background()
	agent, err := ngrok.NewAgent(ngrok.WithAuthtoken(token))
	if err != nil {
		fail("agent: %v", err)
	}
	options := []ngrok.EndpointOption{}
	if len(os.Args) > 2 && os.Args[2] != "" {
		options = append(options, ngrok.WithURL(os.Args[2]))
	}
	forwarder, err := agent.Forward(ctx, ngrok.WithUpstream(os.Args[1]), options...)
	if err != nil {
		fail("%v", err)
	}
	fmt.Printf("URL %s\n", forwarder.URL().String())
	<-forwarder.Done()
	fail("endpoint closed")
}
