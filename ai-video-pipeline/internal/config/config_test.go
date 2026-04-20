package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func writeTestConfig(t *testing.T, content string) string {
	t.Helper()
	dir := t.TempDir()
	path := filepath.Join(dir, "config.yaml")
	if err := os.WriteFile(path, []byte(content), 0644); err != nil {
		t.Fatal(err)
	}
	return path
}

const validYAML = `
deepseek:
  api_key: "sk-test"
  endpoint: "https://api.deepseek.com"
  timeout_seconds: 60
edge_tts:
  voice: "zh-CN-XiaoxiaoNeural"
  output_dir: "./output/audio"
pexels:
  api_key: "px-test"
  output_dir: "./output/media"
ffmpeg:
  path: "/usr/bin/ffmpeg"
  output_dir: "./output/video"
publish:
  platform: "xiaohongshu"
  max_file_size: 104857600
harnesses:
  script:
    max_retries: 3
    retry_interval_ms: 1000
    timeout_seconds: 120
  material:
    max_retries: 2
    retry_interval_ms: 2000
    timeout_seconds: 300
  synthesis:
    max_retries: 1
    retry_interval_ms: 1000
    timeout_seconds: 600
  distribution:
    max_retries: 3
    retry_interval_ms: 3000
    timeout_seconds: 120
`

func TestLoad_ValidConfig(t *testing.T) {
	path := writeTestConfig(t, validYAML)
	cfg, err := Load(path)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.DeepSeek.APIKey != "sk-test" {
		t.Errorf("expected api_key 'sk-test', got %q", cfg.DeepSeek.APIKey)
	}
	if cfg.DeepSeek.Endpoint != "https://api.deepseek.com" {
		t.Errorf("expected endpoint 'https://api.deepseek.com', got %q", cfg.DeepSeek.Endpoint)
	}
	if cfg.DeepSeek.Timeout != 60 {
		t.Errorf("expected timeout 60, got %d", cfg.DeepSeek.Timeout)
	}
	if cfg.EdgeTTS.Voice != "zh-CN-XiaoxiaoNeural" {
		t.Errorf("expected voice 'zh-CN-XiaoxiaoNeural', got %q", cfg.EdgeTTS.Voice)
	}
	if cfg.Pexels.APIKey != "px-test" {
		t.Errorf("expected pexels api_key 'px-test', got %q", cfg.Pexels.APIKey)
	}
	if cfg.FFmpeg.Path != "/usr/bin/ffmpeg" {
		t.Errorf("expected ffmpeg path '/usr/bin/ffmpeg', got %q", cfg.FFmpeg.Path)
	}
	if cfg.Publish.Platform != "xiaohongshu" {
		t.Errorf("expected platform 'xiaohongshu', got %q", cfg.Publish.Platform)
	}
	if cfg.Publish.MaxFileSize != 104857600 {
		t.Errorf("expected max_file_size 104857600, got %d", cfg.Publish.MaxFileSize)
	}
	if cfg.Harnesses.Script.MaxRetries != 3 {
		t.Errorf("expected script max_retries 3, got %d", cfg.Harnesses.Script.MaxRetries)
	}
}

func TestLoad_FileNotFound(t *testing.T) {
	_, err := Load("/nonexistent/config.yaml")
	if err == nil {
		t.Fatal("expected error for missing file")
	}
	if !strings.Contains(err.Error(), "failed to read config file") {
		t.Errorf("expected 'failed to read config file' in error, got: %v", err)
	}
}

func TestLoad_InvalidYAML(t *testing.T) {
	path := writeTestConfig(t, "{{invalid yaml")
	_, err := Load(path)
	if err == nil {
		t.Fatal("expected error for invalid YAML")
	}
	if !strings.Contains(err.Error(), "failed to parse config file") {
		t.Errorf("expected 'failed to parse config file' in error, got: %v", err)
	}
}

func TestLoad_MissingRequiredParams(t *testing.T) {
	// Config with all required fields empty
	yaml := `
deepseek:
  api_key: ""
  endpoint: ""
edge_tts:
  voice: ""
  output_dir: ""
pexels:
  api_key: ""
  output_dir: ""
ffmpeg:
  path: ""
  output_dir: ""
publish:
  platform: ""
`
	path := writeTestConfig(t, yaml)
	_, err := Load(path)
	if err == nil {
		t.Fatal("expected error for missing required params")
	}

	errMsg := err.Error()
	required := []string{
		"deepseek.api_key", "deepseek.endpoint",
		"edge_tts.voice", "edge_tts.output_dir",
		"pexels.api_key", "pexels.output_dir",
		"ffmpeg.path", "ffmpeg.output_dir",
		"publish.platform",
	}
	for _, param := range required {
		if !strings.Contains(errMsg, param) {
			t.Errorf("expected error to contain %q, got: %v", param, errMsg)
		}
	}
}

func TestLoad_EnvOverrides(t *testing.T) {
	path := writeTestConfig(t, validYAML)

	// Set env vars to override config values
	t.Setenv("PIPELINE_DEEPSEEK_API_KEY", "env-sk-key")
	t.Setenv("PIPELINE_DEEPSEEK_ENDPOINT", "https://env.deepseek.com")
	t.Setenv("PIPELINE_PEXELS_API_KEY", "env-px-key")
	t.Setenv("PIPELINE_EDGE_TTS_VOICE", "en-US-JennyNeural")
	t.Setenv("PIPELINE_FFMPEG_PATH", "/opt/ffmpeg")
	t.Setenv("PIPELINE_PUBLISH_PLATFORM", "douyin")

	cfg, err := Load(path)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if cfg.DeepSeek.APIKey != "env-sk-key" {
		t.Errorf("expected env override 'env-sk-key', got %q", cfg.DeepSeek.APIKey)
	}
	if cfg.DeepSeek.Endpoint != "https://env.deepseek.com" {
		t.Errorf("expected env override 'https://env.deepseek.com', got %q", cfg.DeepSeek.Endpoint)
	}
	if cfg.Pexels.APIKey != "env-px-key" {
		t.Errorf("expected env override 'env-px-key', got %q", cfg.Pexels.APIKey)
	}
	if cfg.EdgeTTS.Voice != "en-US-JennyNeural" {
		t.Errorf("expected env override 'en-US-JennyNeural', got %q", cfg.EdgeTTS.Voice)
	}
	if cfg.FFmpeg.Path != "/opt/ffmpeg" {
		t.Errorf("expected env override '/opt/ffmpeg', got %q", cfg.FFmpeg.Path)
	}
	if cfg.Publish.Platform != "douyin" {
		t.Errorf("expected env override 'douyin', got %q", cfg.Publish.Platform)
	}
}

func TestLoad_EnvOverridesFillMissing(t *testing.T) {
	// Config missing api keys, but env vars provide them
	yaml := `
deepseek:
  endpoint: "https://api.deepseek.com"
edge_tts:
  voice: "zh-CN-XiaoxiaoNeural"
  output_dir: "./output/audio"
pexels:
  output_dir: "./output/media"
ffmpeg:
  path: "/usr/bin/ffmpeg"
  output_dir: "./output/video"
publish:
  platform: "xiaohongshu"
`
	path := writeTestConfig(t, yaml)

	t.Setenv("PIPELINE_DEEPSEEK_API_KEY", "env-sk-key")
	t.Setenv("PIPELINE_PEXELS_API_KEY", "env-px-key")

	cfg, err := Load(path)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.DeepSeek.APIKey != "env-sk-key" {
		t.Errorf("expected env override 'env-sk-key', got %q", cfg.DeepSeek.APIKey)
	}
	if cfg.Pexels.APIKey != "env-px-key" {
		t.Errorf("expected env override 'env-px-key', got %q", cfg.Pexels.APIKey)
	}
}

func TestLoad_PartialMissingParams(t *testing.T) {
	// Only deepseek.api_key and pexels.api_key are missing
	yaml := `
deepseek:
  endpoint: "https://api.deepseek.com"
edge_tts:
  voice: "zh-CN-XiaoxiaoNeural"
  output_dir: "./output/audio"
pexels:
  output_dir: "./output/media"
ffmpeg:
  path: "/usr/bin/ffmpeg"
  output_dir: "./output/video"
publish:
  platform: "xiaohongshu"
`
	path := writeTestConfig(t, yaml)
	_, err := Load(path)
	if err == nil {
		t.Fatal("expected error for missing params")
	}

	errMsg := err.Error()
	if !strings.Contains(errMsg, "deepseek.api_key") {
		t.Errorf("expected error to contain 'deepseek.api_key', got: %v", errMsg)
	}
	if !strings.Contains(errMsg, "pexels.api_key") {
		t.Errorf("expected error to contain 'pexels.api_key', got: %v", errMsg)
	}
	// These should NOT be in the error
	if strings.Contains(errMsg, "deepseek.endpoint") {
		t.Errorf("did not expect 'deepseek.endpoint' in error, got: %v", errMsg)
	}
}
