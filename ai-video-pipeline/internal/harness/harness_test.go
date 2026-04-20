package harness

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"testing"
	"time"
)

func testLogger() *slog.Logger {
	return slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelDebug}))
}

// --- ValidateJSONInput tests ---

func TestValidateJSONInput_ValidJSON(t *testing.T) {
	valid := [][]byte{
		[]byte(`{}`),
		[]byte(`{"key":"value"}`),
		[]byte(`[1,2,3]`),
		[]byte(`"hello"`),
		[]byte(`123`),
		[]byte(`true`),
	}
	for _, input := range valid {
		if err := ValidateJSONInput(input); err != nil {
			t.Errorf("ValidateJSONInput(%q) returned error: %v", input, err)
		}
	}
}

func TestValidateJSONInput_InvalidJSON(t *testing.T) {
	invalid := [][]byte{
		nil,
		{},
		[]byte(`{invalid`),
		[]byte(`not json`),
		[]byte(`{"key": }`),
	}
	for _, input := range invalid {
		if err := ValidateJSONInput(input); err == nil {
			t.Errorf("ValidateJSONInput(%q) expected error, got nil", input)
		}
	}
}

// --- BaseHarness.Name tests ---

func TestBaseHarness_Name(t *testing.T) {
	bh := NewBaseHarness("test-harness", RetryConfig{}, testLogger())
	if bh.Name() != "test-harness" {
		t.Errorf("Name() = %q, want %q", bh.Name(), "test-harness")
	}
}

// --- ExecuteWithRetry tests ---

func TestExecuteWithRetry_SuccessOnFirstCall(t *testing.T) {
	bh := NewBaseHarness("test", RetryConfig{
		MaxRetries:    2,
		RetryInterval: 10 * time.Millisecond,
		Timeout:       1 * time.Second,
	}, testLogger())

	callCount := 0
	fn := func(ctx context.Context) ([]byte, error) {
		callCount++
		return []byte(`{"ok":true}`), nil
	}

	result, err := bh.ExecuteWithRetry(context.Background(), fn, nil)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if string(result) != `{"ok":true}` {
		t.Errorf("result = %q, want %q", result, `{"ok":true}`)
	}
	if callCount != 1 {
		t.Errorf("callCount = %d, want 1", callCount)
	}
}

func TestExecuteWithRetry_SuccessAfterRetries(t *testing.T) {
	bh := NewBaseHarness("test", RetryConfig{
		MaxRetries:    3,
		RetryInterval: 10 * time.Millisecond,
		Timeout:       1 * time.Second,
	}, testLogger())

	callCount := 0
	fn := func(ctx context.Context) ([]byte, error) {
		callCount++
		if callCount < 3 {
			return nil, errors.New("temporary failure")
		}
		return []byte(`{"ok":true}`), nil
	}

	result, err := bh.ExecuteWithRetry(context.Background(), fn, nil)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if string(result) != `{"ok":true}` {
		t.Errorf("result = %q, want %q", result, `{"ok":true}`)
	}
	if callCount != 3 {
		t.Errorf("callCount = %d, want 3", callCount)
	}
}

func TestExecuteWithRetry_AllFailNoFallback(t *testing.T) {
	bh := NewBaseHarness("test", RetryConfig{
		MaxRetries:    2,
		RetryInterval: 10 * time.Millisecond,
		Timeout:       1 * time.Second,
	}, testLogger())

	callCount := 0
	fn := func(ctx context.Context) ([]byte, error) {
		callCount++
		return nil, fmt.Errorf("fail #%d", callCount)
	}

	_, err := bh.ExecuteWithRetry(context.Background(), fn, nil)
	if err == nil {
		t.Fatal("expected error, got nil")
	}
	// 1 initial + 2 retries = 3 total
	if callCount != 3 {
		t.Errorf("callCount = %d, want 3", callCount)
	}
}

func TestExecuteWithRetry_AllFailWithFallback(t *testing.T) {
	bh := NewBaseHarness("test", RetryConfig{
		MaxRetries:    1,
		RetryInterval: 10 * time.Millisecond,
		Timeout:       1 * time.Second,
	}, testLogger())

	callCount := 0
	fn := func(ctx context.Context) ([]byte, error) {
		callCount++
		return nil, errors.New("always fails")
	}
	fallback := func(ctx context.Context) ([]byte, error) {
		return []byte(`{"fallback":true}`), nil
	}

	result, err := bh.ExecuteWithRetry(context.Background(), fn, fallback)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if string(result) != `{"fallback":true}` {
		t.Errorf("result = %q, want %q", result, `{"fallback":true}`)
	}
	// 1 initial + 1 retry = 2 total
	if callCount != 2 {
		t.Errorf("callCount = %d, want 2", callCount)
	}
}

func TestExecuteWithRetry_TimeoutTriggered(t *testing.T) {
	bh := NewBaseHarness("test", RetryConfig{
		MaxRetries:    0,
		RetryInterval: 10 * time.Millisecond,
		Timeout:       50 * time.Millisecond,
	}, testLogger())

	fn := func(ctx context.Context) ([]byte, error) {
		select {
		case <-time.After(5 * time.Second):
			return []byte(`done`), nil
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	}

	start := time.Now()
	_, err := bh.ExecuteWithRetry(context.Background(), fn, nil)
	elapsed := time.Since(start)

	if err == nil {
		t.Fatal("expected timeout error, got nil")
	}
	// Should complete within timeout + reasonable margin
	if elapsed > 500*time.Millisecond {
		t.Errorf("took %v, expected ~50ms", elapsed)
	}
}

// --- LogExecution tests ---

func TestLogExecution_DoesNotPanic(t *testing.T) {
	bh := NewBaseHarness("test", RetryConfig{}, testLogger())
	// Should not panic for any combination
	bh.LogExecution([]byte(`{"input":"data"}`), []byte(`{"output":"data"}`), 100*time.Millisecond, nil)
	bh.LogExecution(nil, nil, 0, errors.New("some error"))
}

// --- summarize tests ---

func TestSummarize(t *testing.T) {
	short := []byte("hello")
	if s := summarize(short, 100); s != "hello" {
		t.Errorf("summarize short = %q, want %q", s, "hello")
	}

	long := make([]byte, 200)
	for i := range long {
		long[i] = 'a'
	}
	s := summarize(long, 100)
	if len(s) != 103 { // 100 bytes + "..."
		t.Errorf("summarize long len = %d, want 103", len(s))
	}
}
