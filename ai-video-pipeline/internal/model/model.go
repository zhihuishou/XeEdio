package model

import (
	"encoding/json"
	"fmt"
	"strings"
)

// Scene represents a single scene in the script output.
type Scene struct {
	SceneID     int    `json:"scene_id"`
	Description string `json:"description"`
}

// Narration represents a single narration entry in the script output.
type Narration struct {
	SceneID int    `json:"scene_id"`
	Text    string `json:"text"`
}

// ScriptOutput is the standardized output of the script generation stage.
type ScriptOutput struct {
	Scenes     []Scene     `json:"scenes"`
	Narrations []Narration `json:"narrations"`
}

// MaterialOutput is the standardized output of the material generation stage.
type MaterialOutput struct {
	AudioPaths []string `json:"audio_paths"`
	VideoPaths []string `json:"video_paths"`
}

// SynthesisOutput is the standardized output of the synthesis stage.
type SynthesisOutput struct {
	VideoPath string `json:"video_path"`
}

// DistributionOutput is the standardized output of the distribution stage.
type DistributionOutput struct {
	Status     string `json:"status"`
	PublishURL string `json:"publish_url,omitempty"`
	Error      string `json:"error_message,omitempty"`
}

// PipelineInput represents the input to the pipeline.
type PipelineInput struct {
	Topic string `json:"topic"`
	Title string `json:"title,omitempty"`
}

// StageResult records the execution result of a single pipeline stage.
type StageResult struct {
	StageName string `json:"stage_name"`
	Status    string `json:"status"` // "success" | "failed" | "skipped"
	Duration  int64  `json:"duration_ms"`
	Error     string `json:"error,omitempty"`
}

// PipelineReport is the final report of a pipeline execution.
type PipelineReport struct {
	RunID  string        `json:"run_id"`
	Topic  string        `json:"topic"`
	Stages []StageResult `json:"stages"`
	Status string        `json:"status"` // "success" | "failed"
	Total  int64         `json:"total_ms"`
}

// Validate checks that a ScriptOutput is well-formed.
// It returns an error describing all validation failures.
func (s *ScriptOutput) Validate() error {
	var errs []string

	if len(s.Scenes) == 0 {
		errs = append(errs, "scenes: must not be empty")
	}
	if len(s.Narrations) == 0 {
		errs = append(errs, "narrations: must not be empty")
	}
	if len(s.Scenes) != len(s.Narrations) {
		errs = append(errs, fmt.Sprintf("scenes and narrations: length mismatch (scenes=%d, narrations=%d)", len(s.Scenes), len(s.Narrations)))
	}

	for i, sc := range s.Scenes {
		if sc.SceneID <= 0 {
			errs = append(errs, fmt.Sprintf("scenes[%d].scene_id: must be a positive integer, got %d", i, sc.SceneID))
		}
		if strings.TrimSpace(sc.Description) == "" {
			errs = append(errs, fmt.Sprintf("scenes[%d].description: must not be empty", i))
		}
	}

	for i, n := range s.Narrations {
		if n.SceneID <= 0 {
			errs = append(errs, fmt.Sprintf("narrations[%d].scene_id: must be a positive integer, got %d", i, n.SceneID))
		}
		if strings.TrimSpace(n.Text) == "" {
			errs = append(errs, fmt.Sprintf("narrations[%d].text: must not be empty", i))
		}
	}

	if len(errs) > 0 {
		return fmt.Errorf("ScriptOutput validation failed: %s", strings.Join(errs, "; "))
	}
	return nil
}

// --- Marshal helpers ---

// MarshalScriptOutput serializes a ScriptOutput to JSON bytes.
func MarshalScriptOutput(s *ScriptOutput) ([]byte, error) {
	return json.Marshal(s)
}

// MarshalMaterialOutput serializes a MaterialOutput to JSON bytes.
func MarshalMaterialOutput(m *MaterialOutput) ([]byte, error) {
	return json.Marshal(m)
}

// MarshalSynthesisOutput serializes a SynthesisOutput to JSON bytes.
func MarshalSynthesisOutput(s *SynthesisOutput) ([]byte, error) {
	return json.Marshal(s)
}

// MarshalDistributionOutput serializes a DistributionOutput to JSON bytes.
func MarshalDistributionOutput(d *DistributionOutput) ([]byte, error) {
	return json.Marshal(d)
}

// --- Unmarshal helpers ---

// UnmarshalScriptOutput deserializes JSON bytes into a ScriptOutput.
// Returns an error with field names when the input is invalid.
func UnmarshalScriptOutput(data []byte) (*ScriptOutput, error) {
	var s ScriptOutput
	if err := json.Unmarshal(data, &s); err != nil {
		return nil, fmt.Errorf("ScriptOutput: failed to unmarshal: %w", err)
	}
	var errs []string
	if s.Scenes == nil {
		errs = append(errs, "scenes: required field is missing or null")
	}
	if s.Narrations == nil {
		errs = append(errs, "narrations: required field is missing or null")
	}
	if len(errs) > 0 {
		return nil, fmt.Errorf("ScriptOutput: %s", strings.Join(errs, "; "))
	}
	return &s, nil
}

// UnmarshalMaterialOutput deserializes JSON bytes into a MaterialOutput.
// Returns an error with field names when the input is invalid.
func UnmarshalMaterialOutput(data []byte) (*MaterialOutput, error) {
	var m MaterialOutput
	if err := json.Unmarshal(data, &m); err != nil {
		return nil, fmt.Errorf("MaterialOutput: failed to unmarshal: %w", err)
	}
	var errs []string
	if m.AudioPaths == nil {
		errs = append(errs, "audio_paths: required field is missing or null")
	}
	if m.VideoPaths == nil {
		errs = append(errs, "video_paths: required field is missing or null")
	}
	if len(errs) > 0 {
		return nil, fmt.Errorf("MaterialOutput: %s", strings.Join(errs, "; "))
	}
	return &m, nil
}

// UnmarshalSynthesisOutput deserializes JSON bytes into a SynthesisOutput.
// Returns an error with field names when the input is invalid.
func UnmarshalSynthesisOutput(data []byte) (*SynthesisOutput, error) {
	var s SynthesisOutput
	if err := json.Unmarshal(data, &s); err != nil {
		return nil, fmt.Errorf("SynthesisOutput: failed to unmarshal: %w", err)
	}
	if s.VideoPath == "" {
		return nil, fmt.Errorf("SynthesisOutput: video_path: required field is missing or empty")
	}
	return &s, nil
}

// UnmarshalDistributionOutput deserializes JSON bytes into a DistributionOutput.
// Returns an error with field names when the input is invalid.
func UnmarshalDistributionOutput(data []byte) (*DistributionOutput, error) {
	var d DistributionOutput
	if err := json.Unmarshal(data, &d); err != nil {
		return nil, fmt.Errorf("DistributionOutput: failed to unmarshal: %w", err)
	}
	if d.Status == "" {
		return nil, fmt.Errorf("DistributionOutput: status: required field is missing or empty")
	}
	return &d, nil
}
