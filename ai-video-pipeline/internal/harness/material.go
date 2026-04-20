package harness

import (
	"context"
	"fmt"
	"log/slog"
	"sort"
	"strings"
	"sync"
	"time"

	"ai-video-pipeline/internal/model"
	"ai-video-pipeline/internal/wrapper"
)

// MaterialHarness wraps TTS and Media calls to generate audio and video materials in parallel.
// It combines BaseHarness (retry/timeout/logging) with TTSClient and MediaClient interfaces.
type MaterialHarness struct {
	BaseHarness
	tts   wrapper.TTSClient
	media wrapper.MediaClient
}

// NewMaterialHarness creates a new MaterialHarness.
func NewMaterialHarness(cfg RetryConfig, logger *slog.Logger, tts wrapper.TTSClient, media wrapper.MediaClient) *MaterialHarness {
	return &MaterialHarness{
		BaseHarness: NewBaseHarness("material", cfg, logger),
		tts:         tts,
		media:       media,
	}
}

// Execute receives ScriptOutput JSON, generates audio and media in parallel,
// merges results into MaterialOutput JSON, and returns it.
// On partial failure, it returns partial results along with an error indicating the failed subtask.
func (m *MaterialHarness) Execute(ctx context.Context, input []byte) ([]byte, error) {
	start := time.Now()

	// Validate input JSON
	if err := ValidateJSONInput(input); err != nil {
		m.LogExecution(input, nil, time.Since(start), err)
		return nil, err
	}

	// Parse input as ScriptOutput
	scriptOutput, err := model.UnmarshalScriptOutput(input)
	if err != nil {
		err = fmt.Errorf("material: %w", err)
		m.LogExecution(input, nil, time.Since(start), err)
		return nil, err
	}

	// Validate the ScriptOutput
	if err := scriptOutput.Validate(); err != nil {
		err = fmt.Errorf("material: %w", err)
		m.LogExecution(input, nil, time.Since(start), err)
		return nil, err
	}

	// Use ExecuteWithRetry for overall retry/timeout logic.
	// generateMaterials returns nil error on full success, or returns partial output + error on partial failure.
	// On partial failure, we capture the partial output via closure and return it alongside the error.
	var partialOutput []byte
	output, retryErr := m.ExecuteWithRetry(ctx, func(ctx context.Context) ([]byte, error) {
		result, partialResult, err := m.generateMaterials(ctx, scriptOutput)
		if err != nil {
			partialOutput = partialResult
			return nil, err
		}
		return result, nil
	}, nil)

	if retryErr != nil {
		// Return partial output (if any) alongside the error
		m.LogExecution(input, partialOutput, time.Since(start), retryErr)
		return partialOutput, retryErr
	}

	m.LogExecution(input, output, time.Since(start), nil)
	return output, nil
}

// generateMaterials launches TTS and Media goroutines in parallel, collects results,
// and merges them into a MaterialOutput.
// Returns: (fullOutput, partialOutput, error)
// - On full success: fullOutput is set, partialOutput is nil, error is nil
// - On partial failure: fullOutput is nil, partialOutput has partial results, error describes failed subtasks
// - On marshal error: both outputs nil, error set
func (m *MaterialHarness) generateMaterials(ctx context.Context, script *model.ScriptOutput) ([]byte, []byte, error) {
	var (
		wg         sync.WaitGroup
		ttsErr     error
		mediaErr   error
		audioPaths []string
		videoPaths []string
	)

	// TTS goroutine: synthesize audio for each narration
	wg.Add(1)
	go func() {
		defer wg.Done()
		audioPaths, ttsErr = m.synthesizeAll(ctx, script.Narrations)
	}()

	// Media goroutine: search and download video for each scene
	wg.Add(1)
	go func() {
		defer wg.Done()
		videoPaths, mediaErr = m.searchAll(ctx, script.Scenes)
	}()

	wg.Wait()

	// Build MaterialOutput with whatever results we have
	materialOutput := &model.MaterialOutput{
		AudioPaths: audioPaths,
		VideoPaths: videoPaths,
	}
	// Ensure non-nil slices for JSON serialization
	if materialOutput.AudioPaths == nil {
		materialOutput.AudioPaths = []string{}
	}
	if materialOutput.VideoPaths == nil {
		materialOutput.VideoPaths = []string{}
	}

	// Handle failure cases
	var failedSubtasks []string
	if ttsErr != nil {
		failedSubtasks = append(failedSubtasks, "tts")
	}
	if mediaErr != nil {
		failedSubtasks = append(failedSubtasks, "media")
	}

	if len(failedSubtasks) > 0 {
		partialJSON, marshalErr := model.MarshalMaterialOutput(materialOutput)
		if marshalErr != nil {
			return nil, nil, fmt.Errorf("material: failed to marshal partial output: %w", marshalErr)
		}
		return nil, partialJSON, fmt.Errorf("material: subtask failed: %s", strings.Join(failedSubtasks, ", "))
	}

	// Both succeeded
	output, err := model.MarshalMaterialOutput(materialOutput)
	if err != nil {
		return nil, nil, fmt.Errorf("material: failed to marshal output: %w", err)
	}

	return output, nil, nil
}

// synthesizeAll calls TTS for each narration and returns audio paths sorted by scene_id.
func (m *MaterialHarness) synthesizeAll(ctx context.Context, narrations []model.Narration) ([]string, error) {
	type result struct {
		sceneID int
		path    string
		err     error
	}

	results := make([]result, len(narrations))
	var wg sync.WaitGroup

	for i, n := range narrations {
		wg.Add(1)
		go func(idx int, narration model.Narration) {
			defer wg.Done()
			path, err := m.tts.Synthesize(ctx, narration.SceneID, narration.Text)
			results[idx] = result{sceneID: narration.SceneID, path: path, err: err}
		}(i, n)
	}

	wg.Wait()

	// Check for errors
	for _, r := range results {
		if r.err != nil {
			return nil, fmt.Errorf("tts synthesis failed for scene %d: %w", r.sceneID, r.err)
		}
	}

	// Sort by scene_id ascending
	sort.Slice(results, func(i, j int) bool {
		return results[i].sceneID < results[j].sceneID
	})

	paths := make([]string, len(results))
	for i, r := range results {
		paths[i] = r.path
	}

	return paths, nil
}

// searchAll calls Media for each scene and returns video paths sorted by scene_id.
func (m *MaterialHarness) searchAll(ctx context.Context, scenes []model.Scene) ([]string, error) {
	type result struct {
		sceneID int
		path    string
		err     error
	}

	results := make([]result, len(scenes))
	var wg sync.WaitGroup

	for i, s := range scenes {
		wg.Add(1)
		go func(idx int, scene model.Scene) {
			defer wg.Done()
			path, err := m.media.Search(ctx, scene.SceneID, scene.Description)
			results[idx] = result{sceneID: scene.SceneID, path: path, err: err}
		}(i, s)
	}

	wg.Wait()

	// Check for errors
	for _, r := range results {
		if r.err != nil {
			return nil, fmt.Errorf("media search failed for scene %d: %w", r.sceneID, r.err)
		}
	}

	// Sort by scene_id ascending
	sort.Slice(results, func(i, j int) bool {
		return results[i].sceneID < results[j].sceneID
	})

	paths := make([]string, len(results))
	for i, r := range results {
		paths[i] = r.path
	}

	return paths, nil
}
