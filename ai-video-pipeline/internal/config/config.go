package config

import (
	"fmt"
	"os"
	"strings"

	"gopkg.in/yaml.v3"
)

// Config holds all configuration for the AI video pipeline.
type Config struct {
	DeepSeek  DeepSeekConfig  `yaml:"deepseek"`
	EdgeTTS   EdgeTTSConfig   `yaml:"edge_tts"`
	Pexels    PexelsConfig    `yaml:"pexels"`
	FFmpeg    FFmpegConfig    `yaml:"ffmpeg"`
	Publish   PublishConfig   `yaml:"publish"`
	Harnesses HarnessesConfig `yaml:"harnesses"`
}

// DeepSeekConfig holds DeepSeek LLM API configuration.
type DeepSeekConfig struct {
	APIKey   string `yaml:"api_key"`
	Endpoint string `yaml:"endpoint"`
	Timeout  int    `yaml:"timeout_seconds"`
}

// EdgeTTSConfig holds Edge-TTS voice synthesis configuration.
type EdgeTTSConfig struct {
	Voice     string `yaml:"voice"`
	OutputDir string `yaml:"output_dir"`
}

// PexelsConfig holds Pexels media API configuration.
type PexelsConfig struct {
	APIKey    string `yaml:"api_key"`
	OutputDir string `yaml:"output_dir"`
}

// FFmpegConfig holds FFmpeg rendering configuration.
type FFmpegConfig struct {
	Path      string `yaml:"path"`
	OutputDir string `yaml:"output_dir"`
}

// PublishConfig holds video publishing configuration.
type PublishConfig struct {
	Platform    string `yaml:"platform"`
	MaxFileSize int64  `yaml:"max_file_size"`
}

// HarnessesConfig holds retry configuration for each pipeline stage harness.
type HarnessesConfig struct {
	Script       HarnessRetryConfig `yaml:"script"`
	Material     HarnessRetryConfig `yaml:"material"`
	Synthesis    HarnessRetryConfig `yaml:"synthesis"`
	Distribution HarnessRetryConfig `yaml:"distribution"`
}

// HarnessRetryConfig holds retry strategy parameters for a single harness.
type HarnessRetryConfig struct {
	MaxRetries    int `yaml:"max_retries"`
	RetryInterval int `yaml:"retry_interval_ms"`
	Timeout       int `yaml:"timeout_seconds"`
}

// Load reads configuration from a YAML file at the given path, applies
// environment variable overrides (env vars take priority), and validates
// that all required parameters are present. Returns an error listing all
// missing required parameter names when validation fails.
func Load(path string) (*Config, error) {
	cfg := &Config{}

	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("failed to read config file: %w", err)
	}

	if err := yaml.Unmarshal(data, cfg); err != nil {
		return nil, fmt.Errorf("failed to parse config file: %w", err)
	}

	applyEnvOverrides(cfg)

	if err := validateRequired(cfg); err != nil {
		return nil, err
	}

	return cfg, nil
}

// applyEnvOverrides overrides config values with environment variables when set.
// Environment variable mapping uses PIPELINE_ prefix with uppercase underscore names.
func applyEnvOverrides(cfg *Config) {
	if v := os.Getenv("PIPELINE_DEEPSEEK_API_KEY"); v != "" {
		cfg.DeepSeek.APIKey = v
	}
	if v := os.Getenv("PIPELINE_DEEPSEEK_ENDPOINT"); v != "" {
		cfg.DeepSeek.Endpoint = v
	}
	if v := os.Getenv("PIPELINE_PEXELS_API_KEY"); v != "" {
		cfg.Pexels.APIKey = v
	}
	if v := os.Getenv("PIPELINE_EDGE_TTS_VOICE"); v != "" {
		cfg.EdgeTTS.Voice = v
	}
	if v := os.Getenv("PIPELINE_FFMPEG_PATH"); v != "" {
		cfg.FFmpeg.Path = v
	}
	if v := os.Getenv("PIPELINE_PUBLISH_PLATFORM"); v != "" {
		cfg.Publish.Platform = v
	}
}

// validateRequired checks that all required configuration parameters are non-empty.
// Returns an error listing all missing parameter names.
func validateRequired(cfg *Config) error {
	var missing []string

	if cfg.DeepSeek.APIKey == "" {
		missing = append(missing, "deepseek.api_key")
	}
	if cfg.DeepSeek.Endpoint == "" {
		missing = append(missing, "deepseek.endpoint")
	}
	if cfg.EdgeTTS.Voice == "" {
		missing = append(missing, "edge_tts.voice")
	}
	if cfg.EdgeTTS.OutputDir == "" {
		missing = append(missing, "edge_tts.output_dir")
	}
	if cfg.Pexels.APIKey == "" {
		missing = append(missing, "pexels.api_key")
	}
	if cfg.Pexels.OutputDir == "" {
		missing = append(missing, "pexels.output_dir")
	}
	if cfg.FFmpeg.Path == "" {
		missing = append(missing, "ffmpeg.path")
	}
	if cfg.FFmpeg.OutputDir == "" {
		missing = append(missing, "ffmpeg.output_dir")
	}
	if cfg.Publish.Platform == "" {
		missing = append(missing, "publish.platform")
	}

	if len(missing) > 0 {
		return fmt.Errorf("missing required config parameters: %s", strings.Join(missing, ", "))
	}

	return nil
}
