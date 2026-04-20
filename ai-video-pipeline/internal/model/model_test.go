package model

import (
	"strings"
	"testing"
)

// --- Validate tests ---

func TestValidate_ValidScriptOutput(t *testing.T) {
	so := &ScriptOutput{
		Scenes:     []Scene{{SceneID: 1, Description: "sunrise"}},
		Narrations: []Narration{{SceneID: 1, Text: "hello"}},
	}
	if err := so.Validate(); err != nil {
		t.Fatalf("expected no error, got: %v", err)
	}
}

func TestValidate_EmptyScenes(t *testing.T) {
	so := &ScriptOutput{
		Scenes:     []Scene{},
		Narrations: []Narration{{SceneID: 1, Text: "hello"}},
	}
	err := so.Validate()
	if err == nil {
		t.Fatal("expected error for empty scenes")
	}
	if !strings.Contains(err.Error(), "scenes") {
		t.Fatalf("error should mention 'scenes', got: %v", err)
	}
}

func TestValidate_LengthMismatch(t *testing.T) {
	so := &ScriptOutput{
		Scenes:     []Scene{{SceneID: 1, Description: "a"}},
		Narrations: []Narration{{SceneID: 1, Text: "x"}, {SceneID: 2, Text: "y"}},
	}
	err := so.Validate()
	if err == nil {
		t.Fatal("expected error for length mismatch")
	}
	if !strings.Contains(err.Error(), "length mismatch") {
		t.Fatalf("error should mention 'length mismatch', got: %v", err)
	}
}

func TestValidate_InvalidSceneID(t *testing.T) {
	so := &ScriptOutput{
		Scenes:     []Scene{{SceneID: 0, Description: "a"}},
		Narrations: []Narration{{SceneID: 1, Text: "x"}},
	}
	err := so.Validate()
	if err == nil {
		t.Fatal("expected error for non-positive scene_id")
	}
	if !strings.Contains(err.Error(), "scene_id") {
		t.Fatalf("error should mention 'scene_id', got: %v", err)
	}
}

func TestValidate_EmptyDescription(t *testing.T) {
	so := &ScriptOutput{
		Scenes:     []Scene{{SceneID: 1, Description: "  "}},
		Narrations: []Narration{{SceneID: 1, Text: "x"}},
	}
	err := so.Validate()
	if err == nil {
		t.Fatal("expected error for empty description")
	}
	if !strings.Contains(err.Error(), "description") {
		t.Fatalf("error should mention 'description', got: %v", err)
	}
}

func TestValidate_InvalidNarrationSceneID(t *testing.T) {
	so := &ScriptOutput{
		Scenes:     []Scene{{SceneID: 1, Description: "a"}},
		Narrations: []Narration{{SceneID: -1, Text: "x"}},
	}
	err := so.Validate()
	if err == nil {
		t.Fatal("expected error for negative narration scene_id")
	}
	if !strings.Contains(err.Error(), "narrations[0].scene_id") {
		t.Fatalf("error should mention 'narrations[0].scene_id', got: %v", err)
	}
}

func TestValidate_EmptyNarrationText(t *testing.T) {
	so := &ScriptOutput{
		Scenes:     []Scene{{SceneID: 1, Description: "a"}},
		Narrations: []Narration{{SceneID: 1, Text: ""}},
	}
	err := so.Validate()
	if err == nil {
		t.Fatal("expected error for empty narration text")
	}
	if !strings.Contains(err.Error(), "text") {
		t.Fatalf("error should mention 'text', got: %v", err)
	}
}

// --- Marshal/Unmarshal round-trip tests ---

func TestMarshalUnmarshal_ScriptOutput(t *testing.T) {
	orig := &ScriptOutput{
		Scenes:     []Scene{{SceneID: 1, Description: "sunrise"}, {SceneID: 2, Description: "cafe"}},
		Narrations: []Narration{{SceneID: 1, Text: "hello"}, {SceneID: 2, Text: "world"}},
	}
	data, err := MarshalScriptOutput(orig)
	if err != nil {
		t.Fatalf("marshal error: %v", err)
	}
	got, err := UnmarshalScriptOutput(data)
	if err != nil {
		t.Fatalf("unmarshal error: %v", err)
	}
	if len(got.Scenes) != len(orig.Scenes) || len(got.Narrations) != len(orig.Narrations) {
		t.Fatalf("round-trip length mismatch")
	}
	for i := range orig.Scenes {
		if got.Scenes[i] != orig.Scenes[i] {
			t.Fatalf("scenes[%d] mismatch: got %+v, want %+v", i, got.Scenes[i], orig.Scenes[i])
		}
	}
	for i := range orig.Narrations {
		if got.Narrations[i] != orig.Narrations[i] {
			t.Fatalf("narrations[%d] mismatch: got %+v, want %+v", i, got.Narrations[i], orig.Narrations[i])
		}
	}
}

func TestMarshalUnmarshal_MaterialOutput(t *testing.T) {
	orig := &MaterialOutput{
		AudioPaths: []string{"/tmp/a1.mp3", "/tmp/a2.mp3"},
		VideoPaths: []string{"/tmp/v1.mp4", "/tmp/v2.mp4"},
	}
	data, err := MarshalMaterialOutput(orig)
	if err != nil {
		t.Fatalf("marshal error: %v", err)
	}
	got, err := UnmarshalMaterialOutput(data)
	if err != nil {
		t.Fatalf("unmarshal error: %v", err)
	}
	if len(got.AudioPaths) != len(orig.AudioPaths) || len(got.VideoPaths) != len(orig.VideoPaths) {
		t.Fatalf("round-trip length mismatch")
	}
}

func TestMarshalUnmarshal_SynthesisOutput(t *testing.T) {
	orig := &SynthesisOutput{VideoPath: "/tmp/final.mp4"}
	data, err := MarshalSynthesisOutput(orig)
	if err != nil {
		t.Fatalf("marshal error: %v", err)
	}
	got, err := UnmarshalSynthesisOutput(data)
	if err != nil {
		t.Fatalf("unmarshal error: %v", err)
	}
	if got.VideoPath != orig.VideoPath {
		t.Fatalf("round-trip mismatch: got %q, want %q", got.VideoPath, orig.VideoPath)
	}
}

func TestMarshalUnmarshal_DistributionOutput(t *testing.T) {
	orig := &DistributionOutput{Status: "success", PublishURL: "https://example.com/v/1"}
	data, err := MarshalDistributionOutput(orig)
	if err != nil {
		t.Fatalf("marshal error: %v", err)
	}
	got, err := UnmarshalDistributionOutput(data)
	if err != nil {
		t.Fatalf("unmarshal error: %v", err)
	}
	if got.Status != orig.Status || got.PublishURL != orig.PublishURL {
		t.Fatalf("round-trip mismatch: got %+v, want %+v", got, orig)
	}
}

// --- Unmarshal error tests ---

func TestUnmarshalScriptOutput_InvalidJSON(t *testing.T) {
	_, err := UnmarshalScriptOutput([]byte("not json"))
	if err == nil {
		t.Fatal("expected error for invalid JSON")
	}
	if !strings.Contains(err.Error(), "ScriptOutput") {
		t.Fatalf("error should mention 'ScriptOutput', got: %v", err)
	}
}

func TestUnmarshalScriptOutput_MissingScenes(t *testing.T) {
	_, err := UnmarshalScriptOutput([]byte(`{"narrations":[{"scene_id":1,"text":"hi"}]}`))
	if err == nil {
		t.Fatal("expected error for missing scenes")
	}
	if !strings.Contains(err.Error(), "scenes") {
		t.Fatalf("error should mention 'scenes', got: %v", err)
	}
}

func TestUnmarshalMaterialOutput_InvalidJSON(t *testing.T) {
	_, err := UnmarshalMaterialOutput([]byte("{bad"))
	if err == nil {
		t.Fatal("expected error for invalid JSON")
	}
	if !strings.Contains(err.Error(), "MaterialOutput") {
		t.Fatalf("error should mention 'MaterialOutput', got: %v", err)
	}
}

func TestUnmarshalMaterialOutput_MissingFields(t *testing.T) {
	_, err := UnmarshalMaterialOutput([]byte(`{}`))
	if err == nil {
		t.Fatal("expected error for missing fields")
	}
	if !strings.Contains(err.Error(), "audio_paths") {
		t.Fatalf("error should mention 'audio_paths', got: %v", err)
	}
}

func TestUnmarshalSynthesisOutput_EmptyVideoPath(t *testing.T) {
	_, err := UnmarshalSynthesisOutput([]byte(`{"video_path":""}`))
	if err == nil {
		t.Fatal("expected error for empty video_path")
	}
	if !strings.Contains(err.Error(), "video_path") {
		t.Fatalf("error should mention 'video_path', got: %v", err)
	}
}

func TestUnmarshalDistributionOutput_MissingStatus(t *testing.T) {
	_, err := UnmarshalDistributionOutput([]byte(`{"publish_url":"http://x"}`))
	if err == nil {
		t.Fatal("expected error for missing status")
	}
	if !strings.Contains(err.Error(), "status") {
		t.Fatalf("error should mention 'status', got: %v", err)
	}
}
