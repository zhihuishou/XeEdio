package wrapper

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

// Renderer defines the interface for video rendering, enabling easy mocking in tests.
type Renderer interface {
	// Render builds an FFmpeg command from audio and video input paths, executes it,
	// and returns the absolute path of the output MP4 file.
	Render(ctx context.Context, audioPaths, videoPaths []string) (string, error)
}

// FFmpegRenderer implements Renderer by calling FFmpeg via exec.Command.
type FFmpegRenderer struct {
	ffmpegPath string
	outputDir  string
}

// NewFFmpegRenderer creates a new FFmpegRenderer with the given FFmpeg binary path and output directory.
func NewFFmpegRenderer(ffmpegPath, outputDir string) *FFmpegRenderer {
	return &FFmpegRenderer{
		ffmpegPath: ffmpegPath,
		outputDir:  outputDir,
	}
}

// ValidateFilesExist checks that all given file paths exist on disk.
// It returns a slice of all paths that do not exist.
// This function is exported so it can be reused by Distribution_Harness.
func ValidateFilesExist(paths []string) []string {
	var missing []string
	for _, p := range paths {
		if _, err := os.Stat(p); os.IsNotExist(err) {
			missing = append(missing, p)
		}
	}
	return missing
}

// Render validates all input files, builds an FFmpeg filter_complex command to concatenate
// paired audio/video segments, executes it via exec.CommandContext, and returns the output MP4 path.
//
// The FFmpeg command uses the concat filter:
//
//	ffmpeg -i video1.mp4 -i audio1.mp3 -i video2.mp4 -i audio2.mp3 ...
//	  -filter_complex "[0:v][1:a][2:v][3:a]...concat=n=N:v=1:a=1" -y output.mp4
//
// audioPaths and videoPaths must have the same length and be non-empty.
func (r *FFmpegRenderer) Render(ctx context.Context, audioPaths, videoPaths []string) (string, error) {
	if len(audioPaths) == 0 || len(videoPaths) == 0 {
		return "", fmt.Errorf("render_wrapper: audioPaths and videoPaths must not be empty")
	}
	if len(audioPaths) != len(videoPaths) {
		return "", fmt.Errorf("render_wrapper: audioPaths length (%d) must equal videoPaths length (%d)", len(audioPaths), len(videoPaths))
	}

	// Create output directory if it doesn't exist
	if err := os.MkdirAll(r.outputDir, 0o755); err != nil {
		return "", fmt.Errorf("render_wrapper: failed to create output directory: %w", err)
	}

	// Validate all input files exist
	allPaths := make([]string, 0, len(audioPaths)+len(videoPaths))
	allPaths = append(allPaths, audioPaths...)
	allPaths = append(allPaths, videoPaths...)

	if missing := ValidateFilesExist(allPaths); len(missing) > 0 {
		return "", fmt.Errorf("render_wrapper: missing input files: %s", strings.Join(missing, ", "))
	}

	// Generate unique output filename using timestamp
	outputFilename := fmt.Sprintf("output_%d.mp4", time.Now().UnixNano())
	outputPath := filepath.Join(r.outputDir, outputFilename)

	absPath, err := filepath.Abs(outputPath)
	if err != nil {
		return "", fmt.Errorf("render_wrapper: failed to resolve absolute path: %w", err)
	}

	// Build FFmpeg command arguments
	args := buildFFmpegArgs(audioPaths, videoPaths, absPath)

	// Execute FFmpeg with context for cancellation/timeout support
	cmd := exec.CommandContext(ctx, r.ffmpegPath, args...)

	var stderr bytes.Buffer
	cmd.Stderr = &stderr

	if err := cmd.Run(); err != nil {
		// Check if the context caused the error (timeout or cancellation)
		if ctx.Err() != nil {
			return "", fmt.Errorf("render_wrapper: ffmpeg terminated due to context: %w", ctx.Err())
		}
		return "", fmt.Errorf("render_wrapper: ffmpeg failed: %s: %w", stderr.String(), err)
	}

	return absPath, nil
}

// buildFFmpegArgs constructs the FFmpeg command-line arguments for concatenating
// paired video/audio segments using filter_complex.
//
// For N segments, the inputs are interleaved as: -i video1 -i audio1 -i video2 -i audio2 ...
// The filter_complex maps: [0:v][1:a][2:v][3:a]...concat=n=N:v=1:a=1
func buildFFmpegArgs(audioPaths, videoPaths []string, outputPath string) []string {
	n := len(audioPaths) // same as len(videoPaths)

	args := make([]string, 0, 4*n+6)

	// Add input files: interleave video and audio for each segment
	for i := 0; i < n; i++ {
		args = append(args, "-i", videoPaths[i])
		args = append(args, "-i", audioPaths[i])
	}

	// Build filter_complex string
	var filterParts strings.Builder
	for i := 0; i < n; i++ {
		videoIdx := i * 2
		audioIdx := i*2 + 1
		fmt.Fprintf(&filterParts, "[%d:v][%d:a]", videoIdx, audioIdx)
	}
	fmt.Fprintf(&filterParts, "concat=n=%d:v=1:a=1", n)

	args = append(args, "-filter_complex", filterParts.String())
	args = append(args, "-y", outputPath)

	return args
}
