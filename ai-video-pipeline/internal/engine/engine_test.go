package engine

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"regexp"
	"testing"

	"ai-video-pipeline/internal/harness"
	"ai-video-pipeline/internal/model"
)

// mockHarness is a test double for harness.Harness.
type mockHarness struct {
	name     string
	output   []byte
	err      error
	called   bool
	gotInput []byte
}

func (m *mockHarness) Name() string { return m.name }

func (m *mockHarness) Execute(_ context.Context, input []byte) ([]byte, error) {
	m.called = true
	m.gotInput = input
	return m.output, m.err
}

func TestPipelineEngine_Run_AllSuccess(t *testing.T) {
	logger := slog.Default()

	s1 := &mockHarness{name: "script", output: []byte(`{"scenes":[],"narrations":[]}`)}
	s2 := &mockHarness{name: "material", output: []byte(`{"audio_paths":[],"video_paths":[]}`)}
	s3 := &mockHarness{name: "synthesis", output: []byte(`{"video_path":"/out.mp4"}`)}
	s4 := &mockHarness{name: "distribution", output: []byte(`{"status":"success","publish_url":"https://example.com"}`)}

	stages := []harness.Harness{s1, s2, s3, s4}
	engine := NewPipelineEngine(stages, logger)
	report := engine.Run(context.Background(), "test topic")

	// Verify run_id is UUID v4 format
	uuidRegex := regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)
	if !uuidRegex.MatchString(report.RunID) {
		t.Errorf("run_id %q does not match UUID v4 format", report.RunID)
	}

	if report.Topic != "test topic" {
		t.Errorf("expected topic %q, got %q", "test topic", report.Topic)
	}

	if report.Status != "success" {
		t.Errorf("expected status %q, got %q", "success", report.Status)
	}

	if len(report.Stages) != 4 {
		t.Fatalf("expected 4 stages, got %d", len(report.Stages))
	}

	for i, sr := range report.Stages {
		if sr.Status != "success" {
			t.Errorf("stage %d: expected status %q, got %q", i, "success", sr.Status)
		}
	}

	// Verify data passing: first stage should receive PipelineInput JSON
	var pi model.PipelineInput
	if err := json.Unmarshal(s1.gotInput, &pi); err != nil {
		t.Fatalf("stage 1 input is not valid PipelineInput JSON: %v", err)
	}
	if pi.Topic != "test topic" {
		t.Errorf("stage 1 input topic: expected %q, got %q", "test topic", pi.Topic)
	}

	// Each subsequent stage should receive the previous stage's output
	if string(s2.gotInput) != string(s1.output) {
		t.Errorf("stage 2 input mismatch: got %q, want %q", s2.gotInput, s1.output)
	}
	if string(s3.gotInput) != string(s2.output) {
		t.Errorf("stage 3 input mismatch: got %q, want %q", s3.gotInput, s2.output)
	}
	if string(s4.gotInput) != string(s3.output) {
		t.Errorf("stage 4 input mismatch: got %q, want %q", s4.gotInput, s3.output)
	}

	// All stages should have been called
	for _, s := range []*mockHarness{s1, s2, s3, s4} {
		if !s.called {
			t.Errorf("stage %q was not called", s.name)
		}
	}
}

func TestPipelineEngine_Run_StageFailure(t *testing.T) {
	logger := slog.Default()

	s1 := &mockHarness{name: "script", output: []byte(`{"scenes":[]}`)}
	s2 := &mockHarness{name: "material", err: fmt.Errorf("material generation failed")}
	s3 := &mockHarness{name: "synthesis", output: []byte(`{}`)}
	s4 := &mockHarness{name: "distribution", output: []byte(`{}`)}

	stages := []harness.Harness{s1, s2, s3, s4}
	engine := NewPipelineEngine(stages, logger)
	report := engine.Run(context.Background(), "fail topic")

	if report.Status != "failed" {
		t.Errorf("expected status %q, got %q", "failed", report.Status)
	}

	// Stage 1: success
	if report.Stages[0].Status != "success" {
		t.Errorf("stage 0: expected %q, got %q", "success", report.Stages[0].Status)
	}

	// Stage 2: failed
	if report.Stages[1].Status != "failed" {
		t.Errorf("stage 1: expected %q, got %q", "failed", report.Stages[1].Status)
	}
	if report.Stages[1].Error == "" {
		t.Error("stage 1: expected non-empty error")
	}

	// Stages 3 and 4: skipped
	if report.Stages[2].Status != "skipped" {
		t.Errorf("stage 2: expected %q, got %q", "skipped", report.Stages[2].Status)
	}
	if report.Stages[3].Status != "skipped" {
		t.Errorf("stage 3: expected %q, got %q", "skipped", report.Stages[3].Status)
	}

	// Stages 3 and 4 should NOT have been called
	if s3.called {
		t.Error("stage 3 should not have been called after failure")
	}
	if s4.called {
		t.Error("stage 4 should not have been called after failure")
	}

	// Skipped stages should have duration 0
	if report.Stages[2].Duration != 0 {
		t.Errorf("skipped stage 2: expected duration 0, got %d", report.Stages[2].Duration)
	}
	if report.Stages[3].Duration != 0 {
		t.Errorf("skipped stage 3: expected duration 0, got %d", report.Stages[3].Duration)
	}
}

func TestPipelineEngine_Run_FirstStageFailure(t *testing.T) {
	logger := slog.Default()

	s1 := &mockHarness{name: "script", err: fmt.Errorf("LLM unavailable")}
	s2 := &mockHarness{name: "material"}
	s3 := &mockHarness{name: "synthesis"}
	s4 := &mockHarness{name: "distribution"}

	stages := []harness.Harness{s1, s2, s3, s4}
	engine := NewPipelineEngine(stages, logger)
	report := engine.Run(context.Background(), "topic")

	if report.Status != "failed" {
		t.Errorf("expected status %q, got %q", "failed", report.Status)
	}

	if report.Stages[0].Status != "failed" {
		t.Errorf("stage 0: expected %q, got %q", "failed", report.Stages[0].Status)
	}

	for i := 1; i < 4; i++ {
		if report.Stages[i].Status != "skipped" {
			t.Errorf("stage %d: expected %q, got %q", i, "skipped", report.Stages[i].Status)
		}
	}

	// Only stage 1 should have been called
	if !s1.called {
		t.Error("stage 1 should have been called")
	}
	for _, s := range []*mockHarness{s2, s3, s4} {
		if s.called {
			t.Errorf("stage %q should not have been called", s.name)
		}
	}
}

func TestPipelineEngine_Run_UniqueRunIDs(t *testing.T) {
	logger := slog.Default()

	makeEngine := func() *PipelineEngine {
		s := &mockHarness{name: "s1", output: []byte(`{}`)}
		return NewPipelineEngine([]harness.Harness{s}, logger)
	}

	r1 := makeEngine().Run(context.Background(), "topic1")
	r2 := makeEngine().Run(context.Background(), "topic2")

	if r1.RunID == r2.RunID {
		t.Errorf("expected unique run_ids, both are %q", r1.RunID)
	}
}

func TestPipelineEngine_Run_TotalDuration(t *testing.T) {
	logger := slog.Default()

	s1 := &mockHarness{name: "script", output: []byte(`{}`)}
	s2 := &mockHarness{name: "material", output: []byte(`{}`)}

	stages := []harness.Harness{s1, s2}
	engine := NewPipelineEngine(stages, logger)
	report := engine.Run(context.Background(), "topic")

	// Total should be the sum of individual stage durations
	var sum int64
	for _, sr := range report.Stages {
		sum += sr.Duration
	}
	if report.Total != sum {
		t.Errorf("total %d != sum of stage durations %d", report.Total, sum)
	}
}

func TestPipelineEngine_Run_StageNames(t *testing.T) {
	logger := slog.Default()

	s1 := &mockHarness{name: "script", output: []byte(`{}`)}
	s2 := &mockHarness{name: "material", output: []byte(`{}`)}
	s3 := &mockHarness{name: "synthesis", output: []byte(`{}`)}
	s4 := &mockHarness{name: "distribution", output: []byte(`{}`)}

	stages := []harness.Harness{s1, s2, s3, s4}
	engine := NewPipelineEngine(stages, logger)
	report := engine.Run(context.Background(), "topic")

	expectedNames := []string{"script", "material", "synthesis", "distribution"}
	for i, name := range expectedNames {
		if report.Stages[i].StageName != name {
			t.Errorf("stage %d: expected name %q, got %q", i, name, report.Stages[i].StageName)
		}
	}
}
