package harness

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"path/filepath"
	"time"

	"ai-video-pipeline/internal/config"
	"ai-video-pipeline/internal/model"
	"ai-video-pipeline/internal/wrapper"
)

// DistributionInput is the input to the Distribution_Harness.
// It wraps the video path from SynthesisOutput and an optional title for publishing.
type DistributionInput struct {
	VideoPath string `json:"video_path"`
	Title     string `json:"title"`
}

// DistributionHarness wraps Publisher calls to distribute the final video to a platform.
// It combines BaseHarness (retry/timeout/logging) with a Publisher interface.
type DistributionHarness struct {
	BaseHarness
	publisher wrapper.Publisher
}

// NewDistributionHarness creates a new DistributionHarness.
func NewDistributionHarness(cfg RetryConfig, logger *slog.Logger, publisher wrapper.Publisher) *DistributionHarness {
	return &DistributionHarness{
		BaseHarness: NewBaseHarness("distribution", cfg, logger),
		publisher:   publisher,
	}
}

// Execute receives input JSON containing video_path and title, publishes the video,
// and returns Distribution_Output JSON.
//
// Input can be either:
//   - A DistributionInput JSON with "video_path" and "title" fields
//   - A SynthesisOutput JSON with just "video_path" (title defaults to filename)
//
// On publish success: returns DistributionOutput with status="success" and publish_url.
// On publish failure: returns DistributionOutput with status="failed" and error_message.
// The harness only returns an actual error for input validation or marshal failures.
func (d *DistributionHarness) Execute(ctx context.Context, input []byte) ([]byte, error) {
	start := time.Now()

	// Validate input JSON
	if err := ValidateJSONInput(input); err != nil {
		d.LogExecution(input, nil, time.Since(start), err)
		return nil, err
	}

	// Parse input — try as DistributionInput first to get both video_path and title
	distInput, err := parseDistributionInput(input)
	if err != nil {
		err = fmt.Errorf("distribution: %w", err)
		d.LogExecution(input, nil, time.Since(start), err)
		return nil, err
	}

	// Use ExecuteWithRetry for overall retry/timeout logic
	output, err := d.ExecuteWithRetry(ctx, func(ctx context.Context) ([]byte, error) {
		return d.publish(ctx, distInput)
	}, nil)

	d.LogExecution(input, output, time.Since(start), err)
	return output, err
}

// publish calls the Publisher and builds the DistributionOutput JSON.
// Publisher failures are captured in the output (status="failed") rather than
// returned as errors, so the harness reports success even when publishing fails.
func (d *DistributionHarness) publish(ctx context.Context, input *DistributionInput) ([]byte, error) {
	publishURL, pubErr := d.publisher.Publish(ctx, input.VideoPath, input.Title)

	var distOutput *model.DistributionOutput
	if pubErr != nil {
		distOutput = &model.DistributionOutput{
			Status: "failed",
			Error:  pubErr.Error(),
		}
	} else {
		distOutput = &model.DistributionOutput{
			Status:     "success",
			PublishURL: publishURL,
		}
	}

	result, err := model.MarshalDistributionOutput(distOutput)
	if err != nil {
		return nil, fmt.Errorf("distribution: failed to marshal output: %w", err)
	}

	return result, nil
}

// parseDistributionInput parses the input JSON into a DistributionInput.
// It first tries to unmarshal as a DistributionInput. If the title is empty,
// it falls back to using the video filename as the title.
func parseDistributionInput(input []byte) (*DistributionInput, error) {
	var di DistributionInput
	if err := json.Unmarshal(input, &di); err != nil {
		return nil, fmt.Errorf("failed to parse input: %w", err)
	}

	if di.VideoPath == "" {
		return nil, fmt.Errorf("video_path: required field is missing or empty")
	}

	// Default title to the video filename if not provided
	if di.Title == "" {
		di.Title = filepath.Base(di.VideoPath)
	}

	return &di, nil
}

// NewPublisherFactory selects a Publisher implementation based on the platform name.
// Currently only "xiaohongshu" is supported.
func NewPublisherFactory(platform string, cfg config.PublishConfig, timeout time.Duration) (wrapper.Publisher, error) {
	switch platform {
	case "xiaohongshu":
		// XiaohongshuPublisher needs an API endpoint and key.
		// These would typically come from additional config; for now we use
		// sensible defaults that can be overridden via the PublishConfig.
		return wrapper.NewXiaohongshuPublisher(
			"https://api.xiaohongshu.com",
			"", // API key should be provided via environment or extended config
			cfg.MaxFileSize,
			timeout,
		), nil
	default:
		return nil, fmt.Errorf("unsupported publish platform: %q", platform)
	}
}
