package wrapper

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

// TTSClient interface compliance check
var _ TTSClient = (*EdgeTTSClient)(nil)

func TestNewEdgeTTSClient(t *testing.T) {
	client := NewEdgeTTSClient("zh-CN-XiaoxiaoNeural", "/tmp/tts-output")
	if client.voice != "zh-CN-XiaoxiaoNeural" {
		t.Errorf("expected voice %q, got %q", "zh-CN-XiaoxiaoNeural", client.voice)
	}
	if client.outputDir != "/tmp/tts-output" {
		t.Errorf("expected outputDir %q, got %q", "/tmp/tts-output", client.outputDir)
	}
}

func TestEdgeTTSClient_Synthesize_CreatesOutputDir(t *testing.T) {
	tmpDir := t.TempDir()
	outputDir := filepath.Join(tmpDir, "nested", "tts-output")

	client := NewEdgeTTSClient("zh-CN-XiaoxiaoNeural", outputDir)

	// Use a cancelled context so the command fails fast without needing edge-tts installed.
	// We're testing that the directory gets created before the command runs.
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	_, _ = client.Synthesize(ctx, 1, "test text")

	// Verify the output directory was created
	info, err := os.Stat(outputDir)
	if err != nil {
		t.Fatalf("expected output directory to be created, got error: %v", err)
	}
	if !info.IsDir() {
		t.Fatal("expected output path to be a directory")
	}
}

func TestEdgeTTSClient_Synthesize_OutputFilename(t *testing.T) {
	// Verify the output path follows the scene_{sceneID}.mp3 pattern.
	// We use a helper script that just creates the expected file.
	tmpDir := t.TempDir()
	client := NewEdgeTTSClient("zh-CN-XiaoxiaoNeural", tmpDir)

	// Create a fake edge-tts script that creates the output file
	fakeScript := filepath.Join(tmpDir, "edge-tts")
	scriptContent := `#!/bin/sh
# Parse --write-media argument
while [ "$#" -gt 0 ]; do
  case "$1" in
    --write-media) shift; touch "$1" ;;
  esac
  shift
done
`
	if err := os.WriteFile(fakeScript, []byte(scriptContent), 0o755); err != nil {
		t.Fatalf("failed to write fake script: %v", err)
	}

	// Override PATH so our fake script is found first
	origPath := os.Getenv("PATH")
	os.Setenv("PATH", tmpDir+":"+origPath)
	defer os.Setenv("PATH", origPath)

	result, err := client.Synthesize(context.Background(), 3, "hello world")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	expectedFilename := "scene_3.mp3"
	if filepath.Base(result) != expectedFilename {
		t.Errorf("expected filename %q, got %q", expectedFilename, filepath.Base(result))
	}

	// Verify the path is absolute
	if !filepath.IsAbs(result) {
		t.Errorf("expected absolute path, got %q", result)
	}
}

func TestEdgeTTSClient_Synthesize_CommandFailure(t *testing.T) {
	tmpDir := t.TempDir()
	client := NewEdgeTTSClient("zh-CN-XiaoxiaoNeural", tmpDir)

	// Create a fake edge-tts that fails with stderr output
	fakeScript := filepath.Join(tmpDir, "edge-tts")
	scriptContent := `#!/bin/sh
echo "voice not found" >&2
exit 1
`
	if err := os.WriteFile(fakeScript, []byte(scriptContent), 0o755); err != nil {
		t.Fatalf("failed to write fake script: %v", err)
	}

	origPath := os.Getenv("PATH")
	os.Setenv("PATH", tmpDir+":"+origPath)
	defer os.Setenv("PATH", origPath)

	_, err := client.Synthesize(context.Background(), 1, "test")
	if err == nil {
		t.Fatal("expected error for failed command")
	}

	errMsg := err.Error()
	if !contains(errMsg, "tts_wrapper") {
		t.Errorf("expected error to contain 'tts_wrapper', got: %s", errMsg)
	}
	if !contains(errMsg, "voice not found") {
		t.Errorf("expected error to contain stderr output 'voice not found', got: %s", errMsg)
	}
}

func TestEdgeTTSClient_Synthesize_ContextCancellation(t *testing.T) {
	tmpDir := t.TempDir()
	client := NewEdgeTTSClient("zh-CN-XiaoxiaoNeural", tmpDir)

	ctx, cancel := context.WithCancel(context.Background())
	cancel() // cancel immediately

	_, err := client.Synthesize(ctx, 1, "test")
	if err == nil {
		t.Fatal("expected error for cancelled context")
	}
}
