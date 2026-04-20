package harness

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"testing"
	"time"

	"ai-video-pipeline/internal/model"
)

// mockTTSClient is a mock implementation of wrapper.TTSClient for testing.
type mockTTSClient struct {
	results map[int]string // sceneID -> path
	err     error          // if non-nil, all calls return this error
}

func (m *mockTTSClient) Synthesize(ctx context.Context, sceneID int, text string) (string, error) {
	if m.err != nil {
		return "", m.err
	}
	path, ok := m.results[sceneID]
	if !ok {
		return "", fmt.Errorf("no mock result for scene %d", sceneID)
	}
	return path, nil
}

// mockMediaClient is a mock implementation of wrapper.MediaClient for testing.
type mockMediaClient struct {
	results map[int]string // sceneID -> path
	err     error          // if non-nil, all calls return this error
}

func (m *mockMediaClient) Search(ctx context.Context, sceneID int, description string) (string, error) {
	if m.err != nil {
		return "", m.err
	}
	path, ok := m.results[sceneID]
	if !ok {
		return "", fmt.Errorf("no mock result for scene %d", sceneID)
	}
	return path, nil
}

func newTestMaterialHarness(tts *mockTTSClient, media *mockMediaClient) *MaterialHarness {
	cfg := RetryConfig{
		MaxRetries:    0,
		RetryInterval: 10 * time.Millisecond,
		Timeout:       5 * time.Second,
	}
	return NewMaterialHarness(cfg, slog.Default(), tts, media)
}

func validScriptOutputJSON(t *testing.T) []byte {
	t.Helper()
	so := &model.ScriptOutput{
		Scenes: []model.Scene{
			{SceneID: 1, Description: "sunrise over city"},
			{SceneID: 2, Description: "coffee shop interior"},
			{SceneID: 3, Description: "park at sunset"},
		},
		Narrations: []model.Narration{
			{SceneID: 1, Text: "A new day begins"},
			{SceneID: 2, Text: "A cup of coffee"},
			{SceneID: 3, Text: "Evening calm"},
		},
	}
	data, err := model.MarshalScriptOutput(so)
	if err != nil {
		t.Fatalf("failed to marshal test ScriptOutput: %v", err)
	}
	return data
}

func TestMaterialHarness_Execute_BothSucceed(t *testing.T) {
	tts := &mockTTSClient{
		results: map[int]string{
			1: "/audio/scene_1.mp3",
			2: "/audio/scene_2.mp3",
			3: "/audio/scene_3.mp3",
		},
	}
	media := &mockMediaClient{
		results: map[int]string{
			1: "/video/scene_1.mp4",
			2: "/video/scene_2.mp4",
			3: "/video/scene_3.mp4",
		},
	}

	h := newTestMaterialHarness(tts, media)
	input := validScriptOutputJSON(t)

	output, err := h.Execute(context.Background(), input)
	if err != nil {
		t.Fatalf("expected no error, got: %v", err)
	}

	var mo model.MaterialOutput
	if err := json.Unmarshal(output, &mo); err != nil {
		t.Fatalf("failed to unmarshal output: %v", err)
	}

	// Verify audio_paths sorted by scene_id
	expectedAudio := []string{"/audio/scene_1.mp3", "/audio/scene_2.mp3", "/audio/scene_3.mp3"}
	if len(mo.AudioPaths) != len(expectedAudio) {
		t.Fatalf("expected %d audio paths, got %d", len(expectedAudio), len(mo.AudioPaths))
	}
	for i, p := range mo.AudioPaths {
		if p != expectedAudio[i] {
			t.Errorf("audio_paths[%d] = %q, want %q", i, p, expectedAudio[i])
		}
	}

	// Verify video_paths sorted by scene_id
	expectedVideo := []string{"/video/scene_1.mp4", "/video/scene_2.mp4", "/video/scene_3.mp4"}
	if len(mo.VideoPaths) != len(expectedVideo) {
		t.Fatalf("expected %d video paths, got %d", len(expectedVideo), len(mo.VideoPaths))
	}
	for i, p := range mo.VideoPaths {
		if p != expectedVideo[i] {
			t.Errorf("video_paths[%d] = %q, want %q", i, p, expectedVideo[i])
		}
	}
}

func TestMaterialHarness_Execute_TTSFails(t *testing.T) {
	tts := &mockTTSClient{
		err: fmt.Errorf("tts service unavailable"),
	}
	media := &mockMediaClient{
		results: map[int]string{
			1: "/video/scene_1.mp4",
			2: "/video/scene_2.mp4",
			3: "/video/scene_3.mp4",
		},
	}

	h := newTestMaterialHarness(tts, media)
	input := validScriptOutputJSON(t)

	output, err := h.Execute(context.Background(), input)
	if err == nil {
		t.Fatal("expected error when TTS fails, got nil")
	}

	// Error should mention "tts" subtask
	if !contains(err.Error(), "tts") {
		t.Errorf("error should mention 'tts' subtask, got: %v", err)
	}

	// Should still have partial output with video paths
	if output != nil {
		var mo model.MaterialOutput
		if jsonErr := json.Unmarshal(output, &mo); jsonErr == nil {
			if len(mo.VideoPaths) != 3 {
				t.Errorf("expected 3 video paths in partial result, got %d", len(mo.VideoPaths))
			}
			if len(mo.AudioPaths) != 0 {
				t.Errorf("expected 0 audio paths when TTS fails, got %d", len(mo.AudioPaths))
			}
		}
	}
}

func TestMaterialHarness_Execute_MediaFails(t *testing.T) {
	tts := &mockTTSClient{
		results: map[int]string{
			1: "/audio/scene_1.mp3",
			2: "/audio/scene_2.mp3",
			3: "/audio/scene_3.mp3",
		},
	}
	media := &mockMediaClient{
		err: fmt.Errorf("pexels api error"),
	}

	h := newTestMaterialHarness(tts, media)
	input := validScriptOutputJSON(t)

	output, err := h.Execute(context.Background(), input)
	if err == nil {
		t.Fatal("expected error when Media fails, got nil")
	}

	// Error should mention "media" subtask
	if !contains(err.Error(), "media") {
		t.Errorf("error should mention 'media' subtask, got: %v", err)
	}

	// Should still have partial output with audio paths
	if output != nil {
		var mo model.MaterialOutput
		if jsonErr := json.Unmarshal(output, &mo); jsonErr == nil {
			if len(mo.AudioPaths) != 3 {
				t.Errorf("expected 3 audio paths in partial result, got %d", len(mo.AudioPaths))
			}
			if len(mo.VideoPaths) != 0 {
				t.Errorf("expected 0 video paths when Media fails, got %d", len(mo.VideoPaths))
			}
		}
	}
}

func TestMaterialHarness_Execute_BothFail(t *testing.T) {
	tts := &mockTTSClient{
		err: fmt.Errorf("tts service unavailable"),
	}
	media := &mockMediaClient{
		err: fmt.Errorf("pexels api error"),
	}

	h := newTestMaterialHarness(tts, media)
	input := validScriptOutputJSON(t)

	_, err := h.Execute(context.Background(), input)
	if err == nil {
		t.Fatal("expected error when both fail, got nil")
	}

	// Error should mention both subtasks
	if !contains(err.Error(), "tts") {
		t.Errorf("error should mention 'tts' subtask, got: %v", err)
	}
	if !contains(err.Error(), "media") {
		t.Errorf("error should mention 'media' subtask, got: %v", err)
	}
}

func TestMaterialHarness_Execute_InvalidJSON(t *testing.T) {
	tts := &mockTTSClient{}
	media := &mockMediaClient{}
	h := newTestMaterialHarness(tts, media)

	_, err := h.Execute(context.Background(), []byte("not json"))
	if err == nil {
		t.Fatal("expected error for invalid JSON input")
	}
}

func TestMaterialHarness_Execute_EmptyInput(t *testing.T) {
	tts := &mockTTSClient{}
	media := &mockMediaClient{}
	h := newTestMaterialHarness(tts, media)

	_, err := h.Execute(context.Background(), []byte{})
	if err == nil {
		t.Fatal("expected error for empty input")
	}
}

func TestMaterialHarness_Execute_ResultsSortedBySceneID(t *testing.T) {
	// Scenes are in reverse order to verify sorting
	so := &model.ScriptOutput{
		Scenes: []model.Scene{
			{SceneID: 3, Description: "third scene"},
			{SceneID: 1, Description: "first scene"},
			{SceneID: 2, Description: "second scene"},
		},
		Narrations: []model.Narration{
			{SceneID: 3, Text: "third narration"},
			{SceneID: 1, Text: "first narration"},
			{SceneID: 2, Text: "second narration"},
		},
	}
	input, err := model.MarshalScriptOutput(so)
	if err != nil {
		t.Fatalf("failed to marshal: %v", err)
	}

	tts := &mockTTSClient{
		results: map[int]string{
			1: "/audio/scene_1.mp3",
			2: "/audio/scene_2.mp3",
			3: "/audio/scene_3.mp3",
		},
	}
	media := &mockMediaClient{
		results: map[int]string{
			1: "/video/scene_1.mp4",
			2: "/video/scene_2.mp4",
			3: "/video/scene_3.mp4",
		},
	}

	h := newTestMaterialHarness(tts, media)
	output, err := h.Execute(context.Background(), input)
	if err != nil {
		t.Fatalf("expected no error, got: %v", err)
	}

	var mo model.MaterialOutput
	if err := json.Unmarshal(output, &mo); err != nil {
		t.Fatalf("failed to unmarshal output: %v", err)
	}

	// Results should be sorted by scene_id ascending regardless of input order
	expectedAudio := []string{"/audio/scene_1.mp3", "/audio/scene_2.mp3", "/audio/scene_3.mp3"}
	for i, p := range mo.AudioPaths {
		if p != expectedAudio[i] {
			t.Errorf("audio_paths[%d] = %q, want %q", i, p, expectedAudio[i])
		}
	}

	expectedVideo := []string{"/video/scene_1.mp4", "/video/scene_2.mp4", "/video/scene_3.mp4"}
	for i, p := range mo.VideoPaths {
		if p != expectedVideo[i] {
			t.Errorf("video_paths[%d] = %q, want %q", i, p, expectedVideo[i])
		}
	}
}

func contains(s, substr string) bool {
	return len(s) >= len(substr) && (s == substr || len(s) > 0 && containsSubstr(s, substr))
}

func containsSubstr(s, substr string) bool {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}
