package com.cointray.app

import android.Manifest
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.camera.core.Camera
import androidx.camera.core.CameraSelector
import androidx.camera.core.FocusMeteringAction
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import kotlinx.coroutines.delay
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.viewmodel.compose.viewModel
import com.google.accompanist.permissions.ExperimentalPermissionsApi
import com.google.accompanist.permissions.isGranted
import com.google.accompanist.permissions.rememberPermissionState
import org.json.JSONObject
import java.io.File

private val Board = Color(0xFF1B4A5A)
private val Dark = Color(0xFF0F2E39)
private val Cream = Color(0xFFEFE7D5)
private val Dim = Color(0xFF9FB3B8)
private val Brass = Color(0xFFC08F3C)
private val Oxide = Color(0xFF7FA894)
private val CaptureGreen = Color(0xFF3DDC84)

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            MaterialTheme(colorScheme = darkColorScheme(primary = Brass, background = Board, surface = Dark)) {
                App()
            }
        }
    }
}

@OptIn(ExperimentalPermissionsApi::class)
@Composable
fun App(vm: CoinViewModel = viewModel()) {
    var tab by remember { mutableStateOf("camera") }
    val camPerm = rememberPermissionState(Manifest.permission.CAMERA)
    Scaffold(
        containerColor = Board,
        bottomBar = {
            Row(
                Modifier
                    .fillMaxWidth()
                    .background(Dark)
                    .windowInsetsPadding(WindowInsets.navigationBars)
                    .padding(top = 10.dp, bottom = 18.dp),
                horizontalArrangement = Arrangement.SpaceEvenly,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                listOf("camera" to "Camera", "edit" to "Edit", "tray" to "Tray", "settings" to "Settings").forEach { (id, label) ->
                    TextButton(onClick = { tab = id }) {
                        Text(label, color = if (tab == id) Brass else Dim, fontSize = 14.sp)
                    }
                }
            }
        },
    ) { pad ->
        Column(Modifier.padding(pad).fillMaxSize().imePadding()) {
            Text(
                vm.status,
                color = Cream,
                fontSize = 13.sp,
                modifier = Modifier
                    .fillMaxWidth()
                    .background(Dark)
                    .windowInsetsPadding(WindowInsets.statusBars)
                    .padding(12.dp),
            )
        if (vm.busy) {
                Row(Modifier.padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(Modifier.size(18.dp), color = Brass, strokeWidth = 2.dp)
                    Text("  Working on PC…", color = Dim, fontSize = 12.sp)
                }
            }
            when (tab) {
                "camera" -> {
                    if (!camPerm.status.isGranted) {
                        Column(Modifier.padding(20.dp)) {
                            Text("Camera permission is needed to photograph coins.", color = Cream)
                            Spacer(Modifier.height(12.dp))
                            Button(onClick = { camPerm.launchPermissionRequest() }, colors = goldBtn()) {
                                Text("Allow camera")
                            }
                        }
                    } else CameraPane(vm)
                }
                "edit" -> EditPane(vm)
                "tray" -> TrayPane(vm)
                "settings" -> SettingsPane(vm)
            }
        }
    }
}

@Composable
fun CameraPane(vm: CoinViewModel) {
    val ctx = LocalContext.current
    val owner = LocalLifecycleOwner.current
    val previewView = remember {
        PreviewView(ctx).apply { scaleType = PreviewView.ScaleType.FILL_CENTER }
    }
    val imageCapture = remember { ImageCapture.Builder().build() }
    var camera by remember { mutableStateOf<Camera?>(null) }
    var torchOn by remember { mutableStateOf(false) }
    var zoom2x by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) {
        val future = ProcessCameraProvider.getInstance(ctx)
        future.addListener({
            val provider = future.get()
            val preview = Preview.Builder().build().also { it.setSurfaceProvider(previewView.surfaceProvider) }
            provider.unbindAll()
            camera = provider.bindToLifecycle(owner, CameraSelector.DEFAULT_BACK_CAMERA, preview, imageCapture)
        }, ContextCompat.getMainExecutor(ctx))
    }
    LaunchedEffect(camera, torchOn) {
        val cam = camera ?: return@LaunchedEffect
        if (cam.cameraInfo.hasFlashUnit()) {
            cam.cameraControl.enableTorch(torchOn)
        }
    }
    LaunchedEffect(camera, zoom2x) {
        val cam = camera ?: return@LaunchedEffect
        val maxZ = cam.cameraInfo.zoomState.value?.maxZoomRatio ?: 1f
        val target = if (zoom2x) minOf(2f, maxZ.coerceAtLeast(1f)) else 1f
        cam.cameraControl.setZoomRatio(target)
    }
    LaunchedEffect(vm.flashGreen) {
        if (vm.flashGreen) {
            delay(600)
            vm.clearFlash()
        }
    }
    val pcReady = vm.host.isNotBlank()
    Column(Modifier.fillMaxSize()) {
        Box(
            Modifier
                .weight(1f)
                .fillMaxWidth()
                .pointerInput(camera) {
                    detectTapGestures { tap ->
                        val cam = camera ?: return@detectTapGestures
                        val pt = previewView.meteringPointFactory.createPoint(tap.x, tap.y)
                        val action = FocusMeteringAction.Builder(
                            pt,
                            FocusMeteringAction.FLAG_AF or FocusMeteringAction.FLAG_AE,
                        ).build()
                        cam.cameraControl.startFocusAndMetering(action)
                    }
                },
        ) {
            AndroidView({ previewView }, Modifier.fillMaxSize())
            Canvas(Modifier.fillMaxSize()) {
                val r = size.minDimension * CoinViewModel.GUIDE_RADIUS_FRAC
                val center = Offset(size.width / 2, size.height / 2)
                drawCircle(Brass, r, center, style = Stroke(width = 6f))
                if (vm.flashGreen) {
                    drawCircle(CaptureGreen, r + 8f, center, style = Stroke(width = 22f))
                }
            }
            Row(
                Modifier.align(Alignment.TopCenter).fillMaxWidth().padding(8.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Chip(torchOn, if (torchOn) "Torch on" else "Torch") {
                    torchOn = !torchOn
                }
                Chip(zoom2x, if (zoom2x) "2×" else "1×") {
                    zoom2x = !zoom2x
                }
            }
            Text(
                "Fill the circle · tap to focus · ${if (vm.captureSide == "reverse") "Back" else "Front"}",
                color = Cream,
                modifier = Modifier.align(Alignment.BottomCenter).padding(12.dp),
            )
        }
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(20.dp, Alignment.CenterHorizontally),
        ) {
            PhotoWell(vm, "obverse", "Front")
            PhotoWell(vm, "reverse", "Back")
        }
        Row(Modifier.fillMaxWidth().padding(8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(
                onClick = {
                    val file = File(ctx.cacheDir, "cap_${vm.captureSide}.jpg")
                    val opts = ImageCapture.OutputFileOptions.Builder(file).build()
                    imageCapture.takePicture(
                        opts,
                        ContextCompat.getMainExecutor(ctx),
                        object : ImageCapture.OnImageSavedCallback {
                            override fun onImageSaved(output: ImageCapture.OutputFileResults) {
                                vm.setPhoto(
                                    vm.captureSide,
                                    CoinViewModel.jpegFromFile(
                                        file.absolutePath,
                                        previewView.width,
                                        previewView.height,
                                    ),
                                )
                                if (vm.captureSide == "obverse") vm.captureSide = "reverse"
                            }
                            override fun onError(exc: ImageCaptureException) {
                                vm.status = "Capture failed: ${exc.message}"
                            }
                        },
                    )
                },
                modifier = Modifier.weight(1f),
                colors = goldBtn(),
            ) { Text("Capture ${if (vm.captureSide == "reverse") "Back" else "Front"}") }
            Button(
                onClick = { vm.readCoin() },
                enabled = !vm.busy && pcReady,
                modifier = Modifier.weight(1f),
                colors = goldBtn(),
            ) {
                Text("Read")
            }
        }
        if (!pcReady) {
            Text(
                "Set the PC URL in Settings before Read.",
                color = Brass,
                fontSize = 12.sp,
                modifier = Modifier.padding(horizontal = 12.dp, vertical = 4.dp),
            )
        }
        Spacer(Modifier.height(8.dp))
    }
}

@Composable
fun PhotoWell(vm: CoinViewModel, side: String, label: String) {
    val jpeg = if (side == "reverse") vm.reverseJpeg else vm.obverseJpeg
    val bmp = remember(jpeg) { CoinViewModel.jpegToBitmap(jpeg) }
    val selected = vm.captureSide == side
    val justShot = vm.lastCapturedSide == side
    val ring = when {
        justShot -> CaptureGreen
        selected -> Brass
        else -> Dim
    }
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Box(
            modifier = Modifier
                .size(72.dp)
                .clip(CircleShape)
                .background(Dark)
                .border(3.dp, ring, CircleShape)
                .clickable {
                    vm.captureSide = side
                    vm.status = "Retake $label — hold the coin in the circle, then Capture."
                },
            contentAlignment = Alignment.Center,
        ) {
            if (bmp != null) {
                Image(
                    bitmap = bmp.asImageBitmap(),
                    contentDescription = label,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxSize().clip(CircleShape),
                )
            } else {
                Text(label.take(1), color = Dim, fontSize = 18.sp)
            }
        }
        Text(label, color = Cream, fontSize = 12.sp, modifier = Modifier.padding(top = 4.dp))
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun EditPane(vm: CoinViewModel) {
    val pcReady = vm.host.isNotBlank()
    Column(Modifier.verticalScroll(rememberScrollState()).padding(12.dp)) {
        Text("1 Capture · 2 Read · 3 DATE & STATUS · 4 Price", color = Dim, fontSize = 12.sp)
        Spacer(Modifier.height(8.dp))
        Row(
            Modifier.fillMaxWidth().padding(bottom = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(20.dp, Alignment.CenterHorizontally),
        ) {
            PhotoWell(vm, "obverse", "Front")
            PhotoWell(vm, "reverse", "Back")
        }
        Field("DATE", vm.year) { vm.year = it }
        Field("MINT", vm.mint) { vm.mint = it }
        Field("COUNTRY", vm.country) { vm.country = it }
        FlowRow(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Catalog.COUNTRY_CODES.forEach { (name, code) ->
                Chip(vm.country == name, code) { vm.country = name }
            }
        }
        Field("DENOM", vm.denom) { vm.denom = it }
        Field("SERIES", vm.series) { vm.series = it }
        Field("GRADE", vm.grade) { vm.grade = it }
        Field("CATALOG NAME", vm.catalogName) { vm.catalogName = it }
        Button(
            onClick = { vm.refreshCatalog() },
            modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
            colors = goldBtn(),
        ) { Text("Update catalog") }
        Text("STATUS", color = Oxide, fontSize = 11.sp, modifier = Modifier.padding(top = 8.dp))
        FlowRow(
            Modifier.fillMaxWidth().padding(vertical = 6.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            listOf("Keep", "Sell", "Check", "Face").forEach { d ->
                Chip(vm.disposition == d, d) { vm.disposition = d }
            }
        }
        Spacer(Modifier.height(8.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { vm.priceCoin() }, enabled = !vm.busy && pcReady, colors = goldBtn(), modifier = Modifier.weight(1f)) {
                Text("Price it")
            }
            Button(onClick = { vm.faceSkip() }, enabled = !vm.busy && pcReady, colors = goldBtn(), modifier = Modifier.weight(1f)) {
                Text("Face skip")
            }
        }
        Button(
            onClick = { vm.priceAndNext() },
            enabled = !vm.busy && pcReady,
            modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
            colors = goldBtn(),
        ) {
            Text("Price & Next")
        }
        Button(onClick = { vm.nextCoin() }, modifier = Modifier.fillMaxWidth().padding(top = 8.dp), colors = goldBtn()) {
            Text("Next coin (no price)")
        }
        vm.result?.let { ResultCard(it) }
    }
}

@Composable
fun ResultCard(c: JSONObject) {
    val lo = c.optDouble("valueLow", 0.0)
    val hi = c.optDouble("valueHigh", 0.0)
    val specs = c.optJSONObject("specs")
    val checks = c.optJSONArray("authChecks")
    Column(Modifier.padding(top = 16.dp).fillMaxWidth().background(Dark).padding(12.dp)) {
        val catalog = c.optString("catalogName")
        if (catalog.isNotBlank()) Text(catalog, color = Brass)
        Text(c.optString("identification"), color = Cream, fontSize = 18.sp)
        Text("$%.2f–$%.2f".format(lo, hi), color = Brass, fontSize = 22.sp)
        val avg = c.optDouble("valueAvg", Double.NaN)
        if (!avg.isNaN()) Text("avg $%.2f".format(avg), color = Dim)
        Text(c.optString("marketSummary").ifBlank { c.optString("notes") }, color = Dim, fontSize = 12.sp)
        if (specs != null || (checks != null && checks.length() > 0)) {
            Spacer(Modifier.height(10.dp))
            Text("IN HAND", color = Oxide, fontSize = 12.sp)
            if (specs != null) {
                val bits = mutableListOf<String>()
                if (specs.has("diameter_mm")) bits.add("~${specs.opt("diameter_mm")} mm")
                if (specs.has("weight_g")) bits.add("~${specs.opt("weight_g")} g")
                if (specs.optString("metal").isNotBlank()) bits.add(specs.optString("metal"))
                if (specs.optString("edge").isNotBlank()) bits.add("${specs.optString("edge")} edge")
                if (bits.isNotEmpty()) Text(bits.joinToString(" · "), color = Cream, fontSize = 13.sp)
                val note = specs.optString("note")
                if (note.isNotBlank()) Text(note, color = Dim, fontSize = 12.sp)
            }
            if (checks != null) {
                for (i in 0 until minOf(6, checks.length())) {
                    Text("• ${checks.optString(i)}", color = Dim, fontSize = 12.sp)
                }
            }
        }
    }
}

@Composable
fun TrayPane(vm: CoinViewModel) {
    var open by remember { mutableStateOf(-1) }
    Column(Modifier.verticalScroll(rememberScrollState()).padding(12.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { vm.pullTray() }, enabled = !vm.busy && vm.host.isNotBlank(), colors = goldBtn()) { Text("Pull from PC") }
            Button(onClick = { vm.pushTray() }, enabled = !vm.busy && vm.host.isNotBlank(), colors = goldBtn()) { Text("Push to PC") }
        }
        Text("${vm.tray.length()} coins", color = Dim, modifier = Modifier.padding(vertical = 8.dp))
        for (i in 0 until vm.tray.length()) {
            val c = vm.tray.getJSONObject(i)
            Column(
                Modifier
                    .fillMaxWidth()
                    .padding(vertical = 6.dp)
                    .background(Dark)
                    .clickable { open = if (open == i) -1 else i }
                    .padding(10.dp),
            ) {
                Text(c.optString("catalogName").ifBlank { c.optString("identification") }, color = Cream)
                Text(
                    listOf(c.optString("country"), c.optString("year"), c.optString("disposition")).filter { it.isNotBlank() }.joinToString(" · "),
                    color = Dim,
                    fontSize = 12.sp,
                )
                if (open == i) {
                    val lo = c.optDouble("valueLow", Double.NaN)
                    val hi = c.optDouble("valueHigh", Double.NaN)
                    if (!lo.isNaN() && !hi.isNaN()) {
                        Text("$%.2f–$%.2f".format(lo, hi), color = Brass, fontSize = 16.sp, modifier = Modifier.padding(top = 6.dp))
                    }
                    val avg = c.optDouble("valueAvg", Double.NaN)
                    if (!avg.isNaN()) Text("avg $%.2f".format(avg), color = Dim, fontSize = 12.sp)
                    Text(c.optString("identification"), color = Cream, fontSize = 13.sp)
                    TextButton(onClick = { vm.deleteTrayAt(i); open = -1 }) {
                        Text("Delete this coin", color = Brass)
                    }
                }
            }
        }
    }
}

@Composable
fun SettingsPane(vm: CoinViewModel) {
    val emulator = NetHints.isEmulator()
    Column(Modifier.verticalScroll(rememberScrollState()).padding(16.dp)) {
        Text("Pairing", color = Brass, fontSize = 16.sp)
        Spacer(Modifier.height(6.dp))
        Text("1. On the PC: python coin_tray.py (leave it running).", color = Cream, fontSize = 13.sp)
        Text("2. Copy the printed Phone URL and Token.", color = Cream, fontSize = 13.sp)
        Text(
            if (emulator) {
                "3. Emulator: URL is http://10.0.2.2:8722 (this PC). Paste the Token."
            } else {
                "3. Phone: same Wi‑Fi as the PC. Paste the printed Phone URL (http://YOUR_LAN_IP:8722)."
            },
            color = Cream,
            fontSize = 13.sp,
        )
        Text("5. First coin: Capture Front/Back (torch if indoor), Read, fix DATE + STATUS, Price it. Wells should look tight. Then Price & Next.", color = Cream, fontSize = 13.sp)
        Spacer(Modifier.height(12.dp))
        Field("PC URL", vm.host) { vm.host = it }
        Field("TOKEN", vm.token) { vm.token = it }
        Text("ID MODE", color = Oxide, fontSize = 11.sp)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(vertical = 8.dp)) {
            Chip(vm.idMode == "companion", "Companion (PC)") { vm.idMode = "companion" }
            Chip(vm.idMode == "ondevice", "On-device (later)") { vm.idMode = "ondevice" }
        }
        if (vm.idMode == "ondevice") {
            Text("On-device vision is not shipped yet. Keep Companion (PC) for Read / Price.", color = Brass, fontSize = 13.sp)
        }
        Button(
            onClick = { vm.saveSettings(); vm.ping() },
            enabled = vm.host.isNotBlank() && !vm.busy,
            colors = goldBtn(),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text("Save & ping PC")
        }
        Spacer(Modifier.height(16.dp))
        Text(NetHints.pairingHelp(), color = Dim, fontSize = 12.sp)
        Text("Allow Python in Windows Firewall if ping still fails.", color = Dim, fontSize = 12.sp)
    }
}

@Composable
fun Field(label: String, value: String, on: (String) -> Unit) {
    OutlinedTextField(
        value = value,
        onValueChange = on,
        label = { Text(label) },
        modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
        colors = OutlinedTextFieldDefaults.colors(
            focusedTextColor = Cream,
            unfocusedTextColor = Cream,
            focusedBorderColor = Brass,
            unfocusedBorderColor = Dim,
            focusedLabelColor = Oxide,
            unfocusedLabelColor = Oxide,
        ),
    )
}

@Composable
fun Chip(on: Boolean, label: String, click: () -> Unit) {
    Text(
        label,
        color = if (on) Dark else Cream,
        fontSize = 12.sp,
        modifier = Modifier
            .background(if (on) Brass else Dark)
            .clickable { click() }
            .padding(horizontal = 10.dp, vertical = 6.dp),
    )
}

@Composable
fun goldBtn() = ButtonDefaults.buttonColors(containerColor = Brass, contentColor = Dark)
