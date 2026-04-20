package wrapper

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// Renderer interface compliance check
var _ Renderer = (*FFmpegRenderer)(nil)

func TestNewFFmpegRenderer(t *testing.T) {
	r := NewFFmpegRenderer("/usr/bin/ffmpeg", "/tmp/output")
	if r.ffmpegPath != "/usr/bin/ffmpeg" {
		t.Errorf("expected ffmpegPath %q, got %q", "/usr/bin/ffmpeg", r.ffmpegPath)
	}
	if r.outputDir != "/tmp/output" {
		t.Errorf("expected outputDir %q, got %q", "/tmp/output", r.outputDir)
	}
}

func TestValidateFilesExist_AllExist(t *testing.T) {
	tmpDir := t.TempDir()
	f1 := filepath.Join(tmpDir, "a.mp3")
	f2 := filepath.Join(tmpDir, "b.mp4")
	os.WriteFile(f1, []byte("audio"), 0o644)
	os.WriteFile(f2, []byte("video"), 0o644)

	missing := ValidateFilesExist([]string{f1, f2})
	if len(missing) != 0 {
		t.Errorf("expected no missing files, got %v", missing)
	}
}

func TestValidateFilesExist_SomeMissing(t *testing.T) {
	tmpDir := t.TempDir()
	existing := filepath.Join(tmpDir, "exists.mp3")
	os.WriteFile(existing, []byte("data"), 0o644)

	missing1 := filepath.Join(tmpDir, "nope1.mp3")
	missing2 := filepath.Join(tmpDir, "nope2.mp4")

	result := ValidateFilesExist([]string{existing, missing1, missing2})
	if len(result) != 2 {
		t.Fatalf("expected 2 missing files, got %d: %v", len(result), result)
	}
	for _, m := range []string{missing1, missing2} {
		found := false
		for _, r := range result {
			if r == m {
				found = true
				break
			}
		}
		if !found {
			t.Errorf("expected %q in missing list", m)
		}
	}
}

func TestValidateFilesExist_EmptyList(t *testing.T) {
	missing := ValidateFilesExist([]string{})
	if len(missing) != 0 {
		t.Errorf("expected no missing files for empty input, got %v", missing)
	}
}

func TestBuildFFmpegArgs(t *testing.T) {
	audio := []string{"/tmp/a1.mp3", "/tmp/a2.mp3"}
	video := []string{"/tmp/v1.mp4", "/tmp/v2.mp4"}
	output := "/tmp/out.mp4"

	args := buildFFmpegArgs(audio, video, output)

	// Should contain all input files
	argsStr := strings.Join(args, " ")
	for _, p := range append(audio, video...) {
		if !strings.Contains(argsStr, p) {
			t.Errorf("expected args to contain %q", p)
		}
	}

	// Should contain filter_complex with concat
	if !strings.Contains(argsStr, "-filter_complex") {
		t.Error("expected args to contain -filter_complex")
	}
	if !strings.Contains(argsStr, "concat=n=2:v=1:a=1") {
		t.Errorf("expected concat=n=2:v=1:a=1 in args, got: %s", argsStr)
	}

	// Output should end with .mp4
	lastArg := args[len(args)-1]
	if !strings.HasSuffix(lastArg, ".mp4") {
		t.Errorf("expected output to end with .mp4, got %q", lastArg)
	}

	// Should have -y flag
	if !strings.Contains(argsStr, "-y") {
		t.Error("expected args to contain -y flag")
	}
}

func TestBuildFFmpegArgs_SingleSegment(t *testing.T) {
	audio := []string{"/tmp/a.mp3"}
	video := []string{"/tmp/v.mp4"}
	output := "/tmp/out.mp4"

	args := buildFFmpegArgs(audio, video, output)
	argsStr := strings.Join(args, " ")

	if !strings.Contains(argsStr, "[0:v][1:a]concat=n=1:v=1:a=1") {
		t.Errorf("expected filter for single segment, got: %s", argsStr)
	}
}

func TestBuildFFmpegArgs_InterleavesVideoAudio(t *testing.T) {
	audio := []string{"/tmp/a1.mp3", "/tmp/a2.mp3"}
	video := []string{"/tmp/v1.mp4", "/tmp/v2.mp4"}
	output := "/tmp/out.mp4"

	args := buildFFmpegArgs(audio, video, output)

	// Verify interleaving: -i v1 -i a1 -i v2 -i a2
	expectedOrder := []string{"-i", "/tmp/v1.mp4", "-i", "/tmp/a1.mp3", "-i", "/tmp/v2.mp4", "-i", "/tmp/a2.mp3"}
	for i, expected := range expectedOrder {
		if args[i] != expected {
			t.Errorf("args[%d]: expected %q, got %q", i, expected, args[i])
		}
	}
}

func TestFFmpegRenderer_Render_EmptyPaths(t *testing.T) {
	r := NewFFmpegRenderer("ffmpeg", t.TempDir())

	_, err := r.Render(context.Background(), []string{}, []string{"/tmp/v.mp4"})
	if err == nil {
		t.Fatal("expected error for empty audioPaths")
	}
	if !strings.Contains(err.Error(), "must not be empty") {
		t.Errorf("expected 'must not be empty' in error, got: %s", err.Error())
	}

	_, err = r.Render(context.Background(), []string{"/tmp/a.mp3"}, []string{})
	if err == nil {
		t.Fatal("expected error for empty videoPaths")
	}
}

func TestFFmpegRenderer_Render_LengthMismatch(t *testing.T) {
	r := NewFFmpegRenderer("ffmpeg", t.TempDir())

	_, err := r.Render(context.Background(), []string{"/tmp/a1.mp3"}, []string{"/tmp/v1.mp4", "/tmp/v2.mp4"})
	if err == nil {
		t.Fatal("expected error for length mismatch")
	}
	if !strings.Contains(err.Error(), "must equal") {
		t.Errorf("expected 'must equal' in error, got: %s", err.Error())
	}
}

func TestFFmpegRenderer_Render_MissingInputFiles(t *testing.T) {
	tmpDir := t.TempDir()
	r := NewFFmpegRenderer("ffmpeg", tmpDir)

	missingAudio := "/tmp/nonexistent_audio.mp3"
	missingVideo := "/tmp/nonexistent_video.mp4"

	_, err := r.Render(context.Background(), []string{missingAudio}, []string{missingVideo})
	if err == nil {
		t.Fatal("expected error for missing input files")
	}
	errMsg := err.Error()
	if !strings.Contains(errMsg, "missing input files") {
		t.Errorf("expected 'missing input files' in error, got: %s", errMsg)
	}
	if !strings.Contains(errMsg, missingAudio) {
		t.Errorf("expected missing audio path in error, got: %s", errMsg)
	}
	if !strings.Contains(errMsg, missingVideo) {
		t.Errorf("expected missing video path in error, got: %s", errMsg)
	}
}

func TestFFmpegRenderer_Render_CreatesOutputDir(t *testing.T) {
	tmpDir := t.TempDir()
	outputDir := filepath.Join(tmpDir, "nested", "render-output")

	// Create fake input files
	audioFile := filepath.Join(tmpDir, "a.mp3")
	videoFile := filepath.Join(tmpDir, "v.mp4")
	os.WriteFile(audioFile, []byte("audio"), 0o644)
	os.WriteFile(videoFile, []byte("video"), 0o644)

	// Use a fake ffmpeg that just creates the output file
	fakeFFmpeg := filepath.Join(tmpDir, "ffmpeg")
	scriptContent := `#!/bin/sh
# Find the output file (last argument) and create it
for arg; do :; done
touch "$arg"
`
	os.WriteFile(fakeFFmpeg, []byte(scriptContent), 0o755)

	r := NewFFmpegRenderer(fakeFFmpeg, outputDir)
	_, err := r.Render(context.Background(), []string{audioFile}, []string{videoFile})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	info, err := os.Stat(outputDir)
	if err != nil {
		t.Fatalf("expected output directory to be created: %v", err)
	}
	if !info.IsDir() {
		t.Fatal("expected output path to be a directory")
	}
}

func TestFFmpegRenderer_Render_Success(t *testing.T) {
	tmpDir := t.TempDir()

	// Create fake input files
	audioFile := filepath.Join(tmpDir, "a.mp3")
	videoFile := filepath.Join(tmpDir, "v.mp4")
	os.WriteFile(audioFile, []byte("audio"), 0o644)
	os.WriteFile(videoFile, []byte("video"), 0o644)

	// Create a fake ffmpeg that creates the output file
	fakeFFmpeg := filepath.Join(tmpDir, "ffmpeg")
	scriptContent := `#!/bin/sh
for arg; do :; done
touch "$arg"
`
	os.WriteFile(fakeFFmpeg, []byte(scriptContent), 0o755)

	outputDir := filepath.Join(tmpDir, "output")
	r := NewFFmpegRenderer(fakeFFmpeg, outputDir)

	result, err := r.Render(context.Background(), []string{audioFile}, []string{videoFile})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	// Verify the path is absolute
	if !filepath.IsAbs(result) {
		t.Errorf("expected absolute path, got %q", result)
	}

	// Verify the output ends with .mp4
	if !strings.HasSuffix(result, ".mp4") {
		t.Errorf("expected output to end with .mp4, got %q", result)
	}

	// Verify the file was created
	if _, err := os.Stat(result); os.IsNotExist(err) {
		t.Errorf("expected output file to exist at %q", result)
	}
}

func TestFFmpegRenderer_Render_FFmpegFailure(t *testing.T) {
	tmpDir := t.TempDir()

	audioFile := filepath.Join(tmpDir, "a.mp3")
	videoFile := filepath.Join(tmpDir, "v.mp4")
	os.WriteFile(audioFile, []byte("audio"), 0o644)
	os.WriteFile(videoFile, []byte("video"), 0o644)

	// Create a fake ffmpeg that fails with stderr
	fakeFFmpeg := filepath.Join(tmpDir, "ffmpeg")
	scriptContent := `#!/bin/sh
echo "codec not found" >&2
exit 1
`
	os.WriteFile(fakeFFmpeg, []byte(scriptContent), 0o755)

	outputDir := filepath.Join(tmpDir, "output")
	r := NewFFmpegRenderer(fakeFFmpeg, outputDir)

	_, err := r.Render(context.Background(), []string{audioFile}, []string{videoFile})
	if err == nil {
		t.Fatal("expected error for ffmpeg failure")
	}
	errMsg := err.Error()
	if !strings.Contains(errMsg, "render_wrapper") {
		t.Errorf("expected 'render_wrapper' in error, got: %s", errMsg)
	}
	if !strings.Contains(errMsg, "codec not found") {
		t.Errorf("expected stderr content in error, got: %s", errMsg)
	}
}

func TestFFmpegRenderer_Render_ContextCancellation(t *testing.T) {
	tmpDir := t.TempDir()

	audioFile := filepath.Join(tmpDir, "a.mp3")
	videoFile := filepath.Join(tmpDir, "v.mp4")
	os.WriteFile(audioFile, []byte("audio"), 0o644)
	os.WriteFile(videoFile, []byte("video"), 0o644)

	// Create a fake ffmpeg that sleeps (will be killed by context)
	fakeFFmpeg := filepath.Join(tmpDir, "ffmpeg")
	scriptContent := `#!/bin/sh
sleep 60
`
	os.WriteFile(fakeFFmpeg, []byte(scriptContent), 0o755)

	outputDir := filepath.Join(tmpDir, "output")
	r := NewFFmpegRenderer(fakeFFmpeg, outputDir)

	ctx, cancel := context.WithCancel(context.Background())
	cancel() // cancel immediately

	_, err := r.Render(ctx, []string{audioFile}, []string{videoFile})
	if err == nil {
		t.Fatal("expected error for cancelled context")
	}
}
