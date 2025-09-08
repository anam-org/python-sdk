#!/usr/bin/env python3
"""
Simple microphone test script to validate audio input is working.
This script will record audio from your microphone and play it back.
"""

import sounddevice as sd
import numpy as np
import time
import sys

def test_microphone(duration=5, device=None):
    """Test microphone by recording and playing back audio."""
    
    # Get default device info
    if device is None:
        device_info = sd.query_devices(kind='input')
        device = device_info['name']
        print(f"Using default input device: {device}")
    
    # Audio parameters
    sample_rate = 48000  # Same as used in the SDK
    channels = 1
    
    print(f"\n🎤 Recording for {duration} seconds...")
    print("Speak into your microphone now!")
    
    try:
        # Record audio
        recording = sd.rec(int(duration * sample_rate), 
                          samplerate=sample_rate, 
                          channels=channels, 
                          dtype='float32',
                          device=device)
        
        # Wait for recording to complete
        sd.wait()
        
        # Check if we got any audio
        max_amplitude = np.max(np.abs(recording))
        print(f"\n✅ Recording complete!")
        print(f"Max amplitude: {max_amplitude:.4f}")
        
        if max_amplitude < 0.001:
            print("⚠️  WARNING: No audio detected! Check your microphone.")
            return False
        
        # Play back the recording
        print("\n🔊 Playing back recorded audio...")
        sd.play(recording, sample_rate)
        sd.wait()
        
        print("✅ Playback complete!")
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def list_audio_devices():
    """List all available audio devices."""
    print("\n📋 Available audio devices:")
    print("-" * 50)
    devices = sd.query_devices()
    for i, device in enumerate(devices):
        if device['max_input_channels'] > 0:
            print(f"{i}: {device['name']} (inputs: {device['max_input_channels']})")
    print("-" * 50)

def test_continuous_capture(duration=10):
    """Test continuous audio capture (similar to WebRTC streaming)."""
    print(f"\n🎙️  Testing continuous audio capture for {duration} seconds...")
    print("This simulates how audio would be captured for WebRTC streaming")
    
    sample_rate = 48000
    block_size = 960  # 20ms at 48kHz (WebRTC standard)
    
    audio_blocks = []
    
    def audio_callback(indata, frames, time, status):
        if status:
            print(f"⚠️  Audio callback status: {status}")
        
        # Check if we're getting audio
        max_amp = np.max(np.abs(indata))
        if max_amp > 0.001:
            print(f"📊 Audio detected - amplitude: {max_amp:.4f}")
        
        audio_blocks.append(indata.copy())
    
    try:
        with sd.InputStream(samplerate=sample_rate,
                           blocksize=block_size,
                           channels=1,
                           dtype='float32',
                           callback=audio_callback):
            print("Listening... (speak into microphone)")
            time.sleep(duration)
        
        print(f"\n✅ Captured {len(audio_blocks)} audio blocks")
        
        # Analyze captured audio
        if audio_blocks:
            all_audio = np.concatenate(audio_blocks)
            max_amp = np.max(np.abs(all_audio))
            print(f"Overall max amplitude: {max_amp:.4f}")
            
            if max_amp < 0.001:
                print("⚠️  WARNING: No significant audio detected during capture!")
                return False
            else:
                print("✅ Audio capture working correctly!")
                return True
    
    except Exception as e:
        print(f"❌ Error during continuous capture: {e}")
        return False

if __name__ == "__main__":
    print("🎙️  Anam SDK - Microphone Test Utility")
    print("=" * 50)
    
    # List available devices
    list_audio_devices()
    
    # Test 1: Basic recording
    print("\n📝 Test 1: Basic microphone recording")
    success1 = test_microphone(duration=3)
    
    # Test 2: Continuous capture (WebRTC-style)
    print("\n📝 Test 2: Continuous capture test")
    success2 = test_continuous_capture(duration=5)
    
    # Summary
    print("\n" + "=" * 50)
    print("📊 Test Summary:")
    print(f"  Basic recording: {'✅ PASS' if success1 else '❌ FAIL'}")
    print(f"  Continuous capture: {'✅ PASS' if success2 else '❌ FAIL'}")
    
    if success1 and success2:
        print("\n✅ All tests passed! Your microphone is working correctly.")
        print("The issue is likely in the WebRTC audio track implementation.")
    else:
        print("\n❌ Some tests failed. Please check your microphone settings.")
        print("Make sure your microphone is not muted and has proper permissions.")