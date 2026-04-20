package harness

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"time"
)

// Harness defines the standardized execution interface for pipeline stages.
type Harness interface {
	// Name returns the harness name, used for logging and error reporting.
	Name() string
	// Execute runs the stage logic, receiving JSON input and returning JSON output.
	Execute(ctx context.Context, input []byte) (output []byte, err error)
}

// RetryConfig holds retry/timeout parameters for a harness.
type RetryConfig struct {
	MaxRetries    int           // max retry count
	RetryInterval time.Duration // interval between retries
	Timeout       time.Duration // single call timeout
}

// BaseHarness provides common retry, timeout, fallback, and logging logic.
// Concrete harnesses embed BaseHarness to inherit these capabilities.
type BaseHarness struct {
	name        string
	retryConfig RetryConfig
	logger      *slog.Logger
}

// NewBaseHarness creates a new BaseHarness with the given parameters.
func NewBaseHarness(name string, cfg RetryConfig, logger *slog.Logger) BaseHarness {
	if logger == nil {
		logger = slog.Default()
	}
	return BaseHarness{
		name:        name,
		retryConfig: cfg,
		logger:      logger,
	}
}

// Name returns the harness name.
func (b *BaseHarness) Name() string {
	return b.name
}

// ExecuteWithRetry wraps retry/timeout/fallback logic.
// fn is the actual business logic function.
// fallback is the degradation function called when retries are exhausted (can be nil).
func (b *BaseHarness) ExecuteWithRetry(
	ctx context.Context,
	fn func(ctx context.Context) ([]byte, error),
	fallback func(ctx context.Context) ([]byte, error),
) ([]byte, error) {
	var lastErr error

	// Initial call + MaxRetries retries = N+1 total attempts
	attempts := b.retryConfig.MaxRetries + 1
	for i := 0; i < attempts; i++ {
		// Create a timeout context for this single call
		callCtx, cancel := context.WithTimeout(ctx, b.retryConfig.Timeout)
		result, err := fn(callCtx)
		cancel()

		if err == nil {
			return result, nil
		}

		lastErr = err

		// Check if the parent context is already done — no point retrying
		if ctx.Err() != nil {
			return nil, fmt.Errorf("%s: context cancelled: %w", b.name, ctx.Err())
		}

		// If this is not the last attempt, wait before retrying
		if i < attempts-1 {
			select {
			case <-time.After(b.retryConfig.RetryInterval):
			case <-ctx.Done():
				return nil, fmt.Errorf("%s: context cancelled during retry wait: %w", b.name, ctx.Err())
			}
		}
	}

	// All retries exhausted — try fallback if provided
	if fallback != nil {
		result, err := fallback(ctx)
		if err != nil {
			return nil, fmt.Errorf("%s: fallback failed after %d attempts: %w", b.name, attempts, err)
		}
		return result, nil
	}

	return nil, fmt.Errorf("%s: all %d attempts failed: %w", b.name, attempts, lastErr)
}

// LogExecution records a structured log entry for a harness execution.
// input/output are summarized to the first 100 bytes.
func (b *BaseHarness) LogExecution(input []byte, output []byte, duration time.Duration, err error) {
	status := "success"
	if err != nil {
		status = "failed"
	}

	b.logger.Info("harness execution",
		slog.String("harness", b.name),
		slog.String("input_summary", summarize(input, 100)),
		slog.String("output_summary", summarize(output, 100)),
		slog.Int64("duration_ms", duration.Milliseconds()),
		slog.String("status", status),
	)
}

// ValidateJSONInput checks whether the given byte slice is valid JSON.
// Returns nil if valid, or an error describing why it is not.
func ValidateJSONInput(input []byte) error {
	if len(input) == 0 {
		return fmt.Errorf("input validation failed: empty input")
	}
	if !json.Valid(input) {
		return fmt.Errorf("input validation failed: invalid JSON")
	}
	return nil
}

// summarize returns the first n bytes of data as a string.
// If data is shorter than n, the full content is returned.
func summarize(data []byte, n int) string {
	if len(data) <= n {
		return string(data)
	}
	return string(data[:n]) + "..."
}
