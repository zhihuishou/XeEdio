package wrapper

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
)

// TTSClient defines the interface for text-to-speech synthesis, enabling easy mocking in tests.
type TTSClient interface {
	// Synthesize generates an MP3 audio file for a single narration,
	// returning the absolute file path of the generated MP3.
	Synthesize(ctx context.Context, sceneID int, text string) (string, error)
}

// EdgeTTSClient implements TTSClient by calling the edge-tts CLI tool.
type EdgeTTSClient struct {
	voice     string // Edge-TTS voice name (e.g. "zh-CN-XiaoxiaoNeural")
	outputDir string // Directory to store generated MP3 files
}

// NewEdgeTTSClient creates a new EdgeTTSClient with the given voice name and output directory.
func NewEdgeTTSClient(voice, outputDir string) *EdgeTTSClient {
	return &EdgeTTSClient{
		voice:     voice,
		outputDir: outputDir,
	}
}

// Synthesize generates an MP3 file for the given narration text using edge-tts CLI.
// It creates the output directory if it doesn't exist, runs the edge-tts command,
// and returns the absolute path of the generated MP3 file.
func (t *EdgeTTSClient) Synthesize(ctx context.Context, sceneID int, text string) (string, error) {
	// Create output directory if it doesn't exist
	if err := os.MkdirAll(t.outputDir, 0o755); err != nil {
		return "", fmt.Errorf("tts_wrapper: failed to create output directory: %w", err)
	}

	// Build output file path
	filename := fmt.Sprintf("scene_%d.mp3", sceneID)
	outputPath := filepath.Join(t.outputDir, filename)

	absPath, err := filepath.Abs(outputPath)
	if err != nil {
		return "", fmt.Errorf("tts_wrapper: failed to resolve absolute path: %w", err)
	}

	// Build edge-tts command with context for cancellation support
	cmd := exec.CommandContext(ctx, "edge-tts",
		"--voice", t.voice,
		"--text", text,
		"--write-media", absPath,
	)

	// Capture stderr for error reporting
	var stderr bytes.Buffer
	cmd.Stderr = &stderr

	if err := cmd.Run(); err != nil {
		return "", fmt.Errorf("tts_wrapper: edge-tts failed (scene %d): %s: %w", sceneID, stderr.String(), err)
	}

	return absPath, nil
}
