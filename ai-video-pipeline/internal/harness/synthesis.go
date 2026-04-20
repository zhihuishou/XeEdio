package harness

import (
	"context"
	"fmt"
	"log/slog"
	"time"

	"ai-video-pipeline/internal/model"
	"ai-video-pipeline/internal/wrapper"
)

// SynthesisHarness wraps Renderer calls to synthesize the final video from audio and video materials.
// It combines BaseHarness (retry/timeout/logging) with a Renderer interface.
type SynthesisHarness struct {
	BaseHarness
	render wrapper.Renderer
}

// NewSynthesisHarness creates a new SynthesisHarness.
func NewSynthesisHarness(cfg RetryConfig, logger *slog.Logger, renderer wrapper.Renderer) *SynthesisHarness {
	return &SynthesisHarness{
		BaseHarness: NewBaseHarness("synthesis", cfg, logger),
		render:      renderer,
	}
}

// Execute receives Material_Output JSON, calls the Renderer to synthesize the video,
// and returns Synthesis_Output JSON.
// It uses BaseHarness.ExecuteWithRetry for overall retry/timeout logic.
func (s *SynthesisHarness) Execute(ctx context.Context, input []byte) ([]byte, error) {
	start := time.Now()

	// Validate input JSON
	if err := ValidateJSONInput(input); err != nil {
		s.LogExecution(input, nil, time.Since(start), err)
		return nil, err
	}

	// Parse input as MaterialOutput
	materialOutput, err := model.UnmarshalMaterialOutput(input)
	if err != nil {
		err = fmt.Errorf("synthesis: %w", err)
		s.LogExecution(input, nil, time.Since(start), err)
		return nil, err
	}

	// Use ExecuteWithRetry for overall retry/timeout logic
	output, err := s.ExecuteWithRetry(ctx, func(ctx context.Context) ([]byte, error) {
		return s.synthesize(ctx, materialOutput)
	}, nil)

	s.LogExecution(input, output, time.Since(start), err)
	return output, err
}

// synthesize calls the Renderer with audio and video paths, builds a SynthesisOutput,
// and marshals it to JSON.
func (s *SynthesisHarness) synthesize(ctx context.Context, material *model.MaterialOutput) ([]byte, error) {
	// Call renderer to produce the output MP4
	videoPath, err := s.render.Render(ctx, material.AudioPaths, material.VideoPaths)
	if err != nil {
		return nil, fmt.Errorf("synthesis: render failed: %w", err)
	}

	// Build SynthesisOutput
	synthesisOutput := &model.SynthesisOutput{
		VideoPath: videoPath,
	}

	// Marshal to JSON
	result, err := model.MarshalSynthesisOutput(synthesisOutput)
	if err != nil {
		return nil, fmt.Errorf("synthesis: failed to marshal output: %w", err)
	}

	return result, nil
}
