// Copyright (c) 2024-2026 Yago López Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

package noust

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

const secretToken = "tok_0123456789abcdef"

func newTestClient(t *testing.T, handler http.HandlerFunc) (*Client, *[]time.Duration) {
	t.Helper()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set(VersionHeader, APIVersion)
		handler(w, r)
	}))
	t.Cleanup(server.Close)
	var waits []time.Duration
	client, err := New(Config{URL: server.URL, Token: secretToken, Sleep: func(d time.Duration) { waits = append(waits, d) }})
	if err != nil {
		t.Fatal(err)
	}
	return client, &waits
}

func TestNewRefusesWhatWouldLeakTheToken(t *testing.T) {
	for name, cfg := range map[string]Config{
		"plain http to a remote host": {URL: "http://noust.example.com", Token: "t"},
		"not an http address":         {URL: "ftp://127.0.0.1", Token: "t"},
		"no host":                     {URL: "https://", Token: "t"},
		"no address":                  {URL: "", Token: "t"},
		"no token":                    {URL: "https://noust.example.com", Token: "  "},
	} {
		if _, err := New(cfg); err == nil {
			t.Errorf("%s was accepted", name)
		}
	}
	for _, url := range []string{"http://127.0.0.1:8080", "http://localhost:8080", "http://[::1]:8080", "https://noust.example.com/"} {
		if _, err := New(Config{URL: url, Token: "t"}); err != nil {
			t.Errorf("%s was refused: %v", url, err)
		}
	}
}

func TestACallCarriesTheTokenOnlyInTheHeaderAndSpeaksTheVersionedAddress(t *testing.T) {
	var seen *http.Request
	client, _ := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		seen = r
		_, _ = w.Write([]byte(`{"domain":"shop.example.com","status":"running","name":"shop","active":true,"enabled":true}`))
	})

	app, err := client.GetApp(context.Background(), "shop.example.com")

	if err != nil {
		t.Fatal(err)
	}
	if app.Domain != "shop.example.com" || app.Status != "running" {
		t.Errorf("decoded %+v", app)
	}
	if seen.URL.Path != "/api/v1/apps/shop.example.com" {
		t.Errorf("path %s", seen.URL.Path)
	}
	if seen.Header.Get("Authorization") != "Bearer "+secretToken {
		t.Errorf("the token is not in the Authorization header")
	}
	if strings.Contains(seen.URL.String(), secretToken) {
		t.Errorf("the token is in the address")
	}
}

func TestPathArgumentsAreEscaped(t *testing.T) {
	var seen string
	client, _ := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		seen = r.URL.EscapedPath()
		_, _ = w.Write([]byte(`{}`))
	})

	_ = client.Do(context.Background(), GetApp, Call{Args: []string{"a/b c"}})

	if seen != "/api/v1/apps/a%2Fb%20c" {
		t.Errorf("escaped path %s", seen)
	}
}

func TestTheAPIsRefusalIsKeptWordForWord(t *testing.T) {
	client, _ := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(400)
		_, _ = w.Write([]byte(`{"error":"deployment_error","detail":"The site was refused","hint":"Fix the server name","fields":{"domain":"taken"},"output":"nginx: [emerg] unknown directive \"servr_name\""}`))
	})

	_, err := client.CreateApp(context.Background(), CreateAppRequest{Domain: "x.example.com"})

	api, ok := AsAPIError(err)
	if !ok {
		t.Fatalf("not an APIError: %v", err)
	}
	if api.Status != 400 || api.Code != "deployment_error" || api.Hint != "Fix the server name" {
		t.Errorf("%+v", api)
	}
	if api.Output != `nginx: [emerg] unknown directive "servr_name"` {
		t.Errorf("output was paraphrased: %q", api.Output)
	}
	if api.Fields["domain"] != "taken" {
		t.Errorf("fields %v", api.Fields)
	}
	if strings.Contains(err.Error(), secretToken) {
		t.Error("the token is in the error")
	}
}

func TestSudoModeRefusalHasItsOwnType(t *testing.T) {
	client, _ := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(403)
		_, _ = w.Write([]byte(`{"error":"elevation_required","detail":"Confirm it's you to continue","hint":"POST /api/auth/elevate","fields":null,"output":null}`))
	})

	_, err := client.DeleteDomain(context.Background(), "shop.example.com", "www.shop.example.com")

	var elevation *ElevationRequiredError
	if !errors.As(err, &elevation) {
		t.Fatalf("not an ElevationRequiredError: %v", err)
	}
	if api, ok := AsAPIError(err); !ok || api.Status != 403 {
		t.Errorf("it is also an APIError: %v", err)
	}
}

func TestAnApprovalRequestIsReportedAndNotRetried(t *testing.T) {
	var calls atomic.Int32
	client, _ := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.Header().Set("X-Noust-Approval-Request", "17")
		w.WriteHeader(202)
		_, _ = w.Write([]byte(`{"error":"approval_required","detail":"This needs a second person's approval. Request 17 is waiting","hint":"send it again with the header","fields":{"approval":"17","state":"requested"}}`))
	})

	_, err := client.CreateBackupSchedule(context.Background(), BackupScheduleRequest{Domain: "shop.example.com", Schedule: "daily"})

	var approval *ApprovalRequiredError
	if !errors.As(err, &approval) {
		t.Fatalf("not an ApprovalRequiredError: %v", err)
	}
	if approval.ApprovalID != "17" {
		t.Errorf("approval id %q", approval.ApprovalID)
	}
	if calls.Load() != 1 {
		t.Errorf("the call was made %d times", calls.Load())
	}
}

func TestAnApprovedRequestIsSentAsTheHeader(t *testing.T) {
	var header string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		header = r.Header.Get(ApprovalHeader)
		w.Header().Set(VersionHeader, APIVersion)
		_, _ = w.Write([]byte(`{}`))
	}))
	defer server.Close()
	client, err := New(Config{URL: server.URL, Token: "t", Approval: "17"})
	if err != nil {
		t.Fatal(err)
	}

	_ = client.DeleteBackupSchedule(context.Background(), "shop.example.com")

	if header != "17" {
		t.Errorf("approval header %q", header)
	}
}

func TestAReadIsRepeatedAfterAGatewayErrorAndAChangeIsNot(t *testing.T) {
	var reads, writes atomic.Int32
	client, waits := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet {
			if reads.Add(1) < 3 {
				w.Header().Set("Retry-After", "2")
				w.WriteHeader(503)
				return
			}
			_, _ = w.Write([]byte(`{"domain":"shop.example.com","status":"running","name":"shop","active":true,"enabled":true}`))
			return
		}
		writes.Add(1)
		w.WriteHeader(503)
	})

	if _, err := client.GetApp(context.Background(), "shop.example.com"); err != nil {
		t.Fatalf("the read was not repeated: %v", err)
	}
	err := client.RestartApp(context.Background(), "shop.example.com")

	if err == nil {
		t.Fatal("the change succeeded")
	}
	if reads.Load() != 3 || writes.Load() != 1 {
		t.Errorf("reads %d, writes %d", reads.Load(), writes.Load())
	}
	if len(*waits) != 2 || (*waits)[0] != 2*time.Second {
		t.Errorf("waits %v: the server asked for 2s", *waits)
	}
}

func TestARedirectIsNotFollowedWithTheToken(t *testing.T) {
	var elsewhere atomic.Int32
	other := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { elsewhere.Add(1) }))
	defer other.Close()
	client, _ := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, other.URL, http.StatusFound)
	})

	_, err := client.GetApp(context.Background(), "shop.example.com")

	if err == nil || !strings.Contains(err.Error(), "redirected") {
		t.Errorf("error %v", err)
	}
	if elsewhere.Load() != 0 {
		t.Error("the redirect was followed")
	}
}

func TestAServerOfAnotherAPIVersionIsRefused(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set(VersionHeader, "2")
		_, _ = w.Write([]byte(`{}`))
	}))
	defer server.Close()
	client, _ := New(Config{URL: server.URL, Token: "t"})

	_, err := client.GetApp(context.Background(), "x")

	if err == nil || !strings.Contains(err.Error(), "version 2") {
		t.Errorf("error %v", err)
	}
}

func TestVerifyRecognisesANoustServer(t *testing.T) {
	notNoust := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte(`{"valid":true}`)) }))
	defer notNoust.Close()
	other, _ := New(Config{URL: notNoust.URL, Token: "t"})
	if err := other.Verify(context.Background()); err == nil || !strings.Contains(err.Error(), "does not look like a Noust server") {
		t.Errorf("a server with no version header was accepted: %v", err)
	}

	client, _ := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"valid":true}`))
	})
	if err := client.Verify(context.Background()); err != nil {
		t.Errorf("a Noust server was refused: %v", err)
	}

	refused, _ := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(401)
		_, _ = w.Write([]byte(`{"error":"unauthorized","detail":"Invalid token"}`))
	})
	if err := refused.Verify(context.Background()); err == nil || !strings.Contains(err.Error(), "401") {
		t.Errorf("a bad token was accepted: %v", err)
	}
}

func TestAJobIsWaitedForAndAFailureCarriesTheServersWords(t *testing.T) {
	var polls atomic.Int32
	client, _ := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		state := "running"
		failure := "null"
		switch r.URL.Path {
		case "/api/v1/jobs/aaaa":
			if polls.Add(1) >= 3 {
				state = "succeeded"
			}
		case "/api/v1/jobs/bbbb":
			state = "failed"
			failure = `"Release 1 did not pass its health check\n  Details: The application did not answer the health check."`
		}
		_, _ = w.Write([]byte(`{"id":"x","name":"Deploy shop","state":"` + state + `","error":` + failure + `}`))
	})

	job, err := client.WaitForJob(context.Background(), &JobAccepted{JobID: "aaaa"}, time.Millisecond, time.Minute)
	if err != nil || job.State != "succeeded" {
		t.Fatalf("job %+v, err %v", job, err)
	}
	if polls.Load() != 3 {
		t.Errorf("polled %d times", polls.Load())
	}

	_, err = client.WaitForJob(context.Background(), &JobAccepted{JobID: "bbbb"}, time.Millisecond, time.Minute)
	var failed *JobFailedError
	if !errors.As(err, &failed) {
		t.Fatalf("not a JobFailedError: %v", err)
	}
	if !strings.Contains(err.Error(), "The application did not answer the health check.") {
		t.Errorf("the failure was paraphrased: %v", err)
	}
}

func TestAWaitThatRunsOutLeavesTheJobRunning(t *testing.T) {
	client, _ := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"id":"cccc","name":"Deploy shop","state":"running"}`))
	})

	_, err := client.WaitForJob(context.Background(), &JobAccepted{JobID: "cccc"}, time.Millisecond, 20*time.Millisecond)

	var timeout *JobTimeoutError
	if !errors.As(err, &timeout) {
		t.Fatalf("not a JobTimeoutError: %v", err)
	}
	if !strings.Contains(err.Error(), "keeps running on the server") {
		t.Errorf("%v", err)
	}
}

func TestAnAcceptedResponseWithoutAJobIsAnError(t *testing.T) {
	client, _ := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {})

	if _, err := client.WaitForJob(context.Background(), nil, time.Millisecond, time.Second); err == nil {
		t.Error("waited for no job")
	}
}

func TestDeprecationIsAnnounced(t *testing.T) {
	var announced string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set(VersionHeader, APIVersion)
		w.Header().Set("Deprecation", "@1700000000")
		w.Header().Set("Sunset", "Wed, 01 Jan 2031 00:00:00 GMT")
		_, _ = w.Write([]byte(`{}`))
	}))
	defer server.Close()
	client, _ := New(Config{URL: server.URL, Token: "t", OnDeprecated: func(op Op, header http.Header) { announced = op.Name + " " + header.Get("Sunset") }})

	_, _ = client.GetApp(context.Background(), "x")

	if announced != "GetApp Wed, 01 Jan 2031 00:00:00 GMT" {
		t.Errorf("announced %q", announced)
	}
}

func TestAContractNestedInDetailIsUnwrapped(t *testing.T) {
	client, _ := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(403)
		_, _ = w.Write([]byte(`{"detail":{"error":"elevation_required","detail":"Confirm it's you to continue","hint":"POST /api/auth/elevate","fields":null}}`))
	})

	err := client.RestartApp(context.Background(), "x")

	var elevation *ElevationRequiredError
	if !errors.As(err, &elevation) {
		t.Fatalf("not an ElevationRequiredError: %v", err)
	}
	if elevation.Detail != "Confirm it's you to continue" || elevation.Hint != "POST /api/auth/elevate" {
		t.Errorf("%+v", elevation.APIError)
	}
}

func TestAChangeIsRepeatedWhenAnotherOperationHoldsTheApplicationAndNotAfterAnythingElse(t *testing.T) {
	var calls atomic.Int32
	client, waits := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		if calls.Add(1) <= 3 {
			w.WriteHeader(409)
			_, _ = w.Write([]byte(`{"error":"app_busy","detail":"Cannot start the domain change: a deploy is still running on it"}`))
			return
		}
		_, _ = w.Write([]byte(`{"app":"shop.example.com","domains":[],"tls":false}`))
	})

	change, err := client.AddDomain(context.Background(), "shop.example.com", AddDomainRequest{Domain: "alt.shop.example.com"})

	if err != nil || change.App != "shop.example.com" {
		t.Fatalf("change %+v, err %v", change, err)
	}
	if calls.Load() != 4 {
		t.Errorf("the change was made %d times, want 3 refusals and 1 success", calls.Load())
	}
	if len(*waits) != 3 || (*waits)[0] != time.Second || (*waits)[1] != 2*time.Second || (*waits)[2] != 4*time.Second {
		t.Errorf("waits %v, want 1s 2s 4s", *waits)
	}

	// Any other conflict is the caller's to read, and is made once.
	calls.Store(0)
	other, _ := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.WriteHeader(409)
		_, _ = w.Write([]byte(`{"error":"conflict","detail":"The domain is taken"}`))
	})
	if _, err := other.AddDomain(context.Background(), "shop.example.com", AddDomainRequest{Domain: "taken.example.com"}); err == nil {
		t.Fatal("a conflict succeeded")
	}
	if calls.Load() != 1 {
		t.Errorf("a plain conflict was made %d times", calls.Load())
	}
}

func TestTheBusyWaitEndsWhenTheServerStaysBusy(t *testing.T) {
	var calls atomic.Int32
	client, _ := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.WriteHeader(409)
		_, _ = w.Write([]byte(`{"error":"app_busy","detail":"a deploy is still running on it"}`))
	})

	err := client.RestartApp(context.Background(), "shop.example.com")

	if api, ok := AsAPIError(err); !ok || api.Code != "app_busy" {
		t.Errorf("error %v", err)
	}
	if calls.Load() != maxBusyAttempts+1 {
		t.Errorf("tried %d times, want %d", calls.Load(), maxBusyAttempts+1)
	}
}
