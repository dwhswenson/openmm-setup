import simtk.openmm as mm
import simtk.openmm.app as app
from flask import Flask, request, session, g, render_template, make_response, send_file
from werkzeug.utils import secure_filename
from multiprocessing import Process, Pipe
import datetime
import os
import shutil
import sys
import tempfile
import zipfile

app = Flask(__name__)
app.config.from_object(__name__)
app.config.update({'SECRET_KEY':'development key'})
app.jinja_env.globals['mm'] = mm

uploadedFiles = {}
scriptOutput = None
simulationProcess = None

def saveUploadedFiles():
    uploadedFiles.clear()
    for key in request.files:
        file = request.files[key]
        temp = tempfile.TemporaryFile()
        shutil.copyfileobj(file, temp)
        uploadedFiles[key] = (temp, secure_filename(file.filename))

@app.route('/')
def showSelectFileType():
    return render_template('selectFileType.html')

@app.route('/selectFiles')
def selectFiles():
    session['fileType'] = request.args.get('type', '')
    return showConfigureFiles()

def showConfigureFiles():
    try:
        fileType = session['fileType']
        if fileType in ('pdb', 'pdbx'):
            return render_template('configurePdbFile.html')
        elif fileType == 'amber':
            return render_template('configureAmberFiles.html')
        elif fileType == 'gromacs':
            return render_template('configureGromacsFiles.html')
    except:
        app.logger.error('Error displaying configure files page', exc_info=True)
    # The file type is invalid, so send them back to the select file type page.
    return showSelectFileType()

@app.route('/configureFiles', methods=['POST'])
def configureFiles():
    fileType = session['fileType']
    if fileType in ('pdb', 'pdbx'):
        if 'file' not in request.files or request.files['file'].filename == '':
            # They didn't select a file.  Send them back.
            return showConfigureFiles()
        saveUploadedFiles()
        session['forcefield'] = request.form.get('forcefield', '')
        session['waterModel'] = request.form.get('waterModel', '')
        session['amoebaWaterModel'] = request.form.get('amoebaWaterModel', '')
    elif fileType == 'amber':
        if 'prmtopFile' not in request.files or request.files['prmtopFile'].filename == '' or 'inpcrdFile' not in request.files or request.files['inpcrdFile'].filename == '':
            # They didn't select a file.  Send them back.
            return showConfigureFiles()
        saveUploadedFiles()
    elif fileType == 'gromacs':
        if 'topFile' not in request.files or request.files['topFile'].filename == '' or 'groFile' not in request.files or request.files['groFile'].filename == '':
            # They didn't select a file.  Send them back.
            return showConfigureFiles()
        saveUploadedFiles()
        session['gromacsIncludeDir'] = request.form.get('gromacsIncludeDir', '')
    configureDefaultOptions()
    return showSimulationOptions()

def showSimulationOptions():
    return render_template('simulationOptions.html')

@app.route('/setSimulationOptions', methods=['POST'])
def setSimulationOptions():
    for key in request.form:
        session[key] = request.form[key]
    session['writeDCD'] = 'writeDCD' in request.form
    session['writeData'] = 'writeData' in request.form
    session['dataFields'] = request.form.getlist('dataFields')
    return createScript()

@app.route('/downloadScript')
def downloadScript():
    response = make_response(createScript())
    response.headers['Content-Disposition'] = 'attachment; filename="run_openmm_simulation.py"'
    return response

@app.route('/downloadPackage')
def downloadPackage():
    temp = tempfile.NamedTemporaryFile()
    with zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED) as zip:
        zip.writestr('openmm_simulation/run_openmm_simulation.py', createScript())
        for key in uploadedFiles:
            file, name = uploadedFiles[key]
            file.seek(0, 0)
            zip.writestr('openmm_simulation/%s' % name, file.read())
    temp.seek(0, 0)
    return send_file(temp, 'application/zip', True, 'openmm_simulation.zip', cache_timeout=0)

@app.route('/showRunSimulation')
def showRunSimulation():
    homeDir = os.path.expanduser('~')
    defaultDir = os.path.join(homeDir, 'openmm_simulation')
    return render_template('runSimulation.html', defaultDir=defaultDir)

@app.route('/startSimulation', methods=['POST'])
def startSimulation():
    global scriptOutput, simulationProcess
    conn1, conn2 = Pipe()
    scriptOutput = conn1
    # Create the simulation directory and copy files.
    try:
        outputDir = request.form['directory']
        if not os.path.isdir(outputDir):
            os.makedirs(outputDir)
    except:
        conn2.send('An error occurred while creating the simulation directory: %s' % sys.exc_info()[1])
        conn2.send(None)
        return ""
    try:
        for key in uploadedFiles:
            file, name = uploadedFiles[key]
            file.seek(0, 0)
            with open(os.path.join(outputDir, name), 'wb') as outputFile:
                shutil.copyfileobj(file, outputFile)
        with open(os.path.join(outputDir, 'run_openmm_simulation.py'), 'w') as outputFile:
            outputFile.write(createScript())
    except:
        conn2.send('An error occurred while copying the input files: %s' % sys.exc_info()[1])
        conn2.send(None)
        return ""
    # Run the simulation in a subprocess.
    simulationProcess = Process(target=simulate, args=(conn2, outputDir))
    simulationProcess.start()
    return ""

@app.route('/stopSimulation', methods=['POST'])
def stopSimulation():
    global scriptOutput, simulationProcess
    simulationProcess.terminate()
    scriptOutput = None
    return ""

@app.route('/getSimulationOutput')
def getSimulationOutput():
    global scriptOutput
    if scriptOutput is None:
        return "", 404
    output = []
    try:
        while scriptOutput.poll():
            data = scriptOutput.recv()
            if data is None:
                scriptOutput = None
                break
            else:
                output.append(data)
    except EOFError:
        scriptOutput = None
    return "".join(output)

def simulate(output, outputDir):
    script = createScript(True)
    exec(script, {"output":output, "outputDir":outputDir})
    output.send(None)

def configureDefaultOptions():
    """Select default options based on the file format and force field."""
    session['ensemble'] = 'npt'
    session['platform'] = 'CUDA'
    session['precision'] = 'single'
    session['cutoff'] = '1.0'
    session['ewaldTol'] = '0.0005'
    session['constraintTol'] = '0.000001'
    session['dt'] = '0.002'
    session['steps'] = '1000000'
    session['equilibrationSteps'] = '1000'
    session['temperature'] = '300'
    session['friction'] = '1.0'
    session['pressure'] = '1.0'
    session['barostatInterval'] = '25'
    session['nonbondedMethod'] = 'PME'
    session['writeDCD'] = True
    session['dcdFilename'] = 'trajectory.dcd'
    session['dcdInterval'] = '10000'
    session['writeData'] = True
    session['dataFilename'] = 'log.txt'
    session['dataInterval'] = '1000'
    session['dataFields'] = ['step', 'speed' ,'progress', 'potentialEnergy', 'temperature']
    isAmoeba = session['fileType'] in ('pdb', 'pdbx') and 'amoeba' in session['forcefield']
    if isAmoeba:
        session['constraints'] = 'none'
    else:
        session['constraints'] = 'hbonds'

def createScript(isInternal=False):
    script = []

    # If we are creating this script for internal use to run a simulation directly, add extra code at the top
    # to set the working directory and redirect stdout to the pipe.

    if isInternal:
        script.append("""
import os
import sys
import time

class PipeOutput(object):
    def write(self, string):
        output.send(string)

sys.stdout = PipeOutput()
sys.stderr = PipeOutput()
os.chdir(outputDir)""")

    # Header
    
    script.append('# This script was generated by OpenMM-Setup on %s.\n' % datetime.date.today())
    script.append('from simtk.openmm import *')
    script.append('from simtk.openmm.app import *')
    script.append('from simtk.unit import *')
    
    # Input files
    
    script.append('\n# Input Files\n')
    fileType = session['fileType']
    if fileType == 'pdb':
        script.append("pdb = PDBFile('%s')" % uploadedFiles['file'][1])
    elif fileType == 'pdbx':
        script.append("pdbx = PDBxFile('%s')" % uploadedFiles['file'][1])
    if fileType in ('pdb', 'pdbx'):
        forcefield = session['forcefield']
        water = session['waterModel']
        if forcefield == 'amoeba2013.xml':
            water = ('amoeba2013_gk.xml' if session['amoebaWaterModel'] == 'implicit' else None)
        elif forcefield == 'charmm_polar_2013.xml':
            water = None
        elif water == 'implicit':
            models = {'amber99sb.xml': 'amber99_obc.xml',
                      'amber99sbildn.xml': 'amber99_obc.xml',
                      'amber03.xml': 'amber03_obc.xml',
                      'amber10.xml': 'amber10_obc.xml'}
            water = models[forcefield]
        if water is None:
            script.append("forcefield = ForceField('%s')" % forcefield)
        else:
            script.append("forcefield = ForceField('%s', '%s')" % (forcefield, water))
    elif fileType == 'amber':
        script.append("prmtop = AmberPrmtopFile('%s')" % uploadedFiles['prmtopFile'][1])
        script.append("inpcrd = AmberInpcrdFile('%s')" % uploadedFiles['inpcrdFile'][1])
    elif fileType == 'gromacs':
        script.append("gro = GromacsGroFile('%s')" % uploadedFiles['groFile'][1])
        script.append("top = GromacsTopFile('%s', includeDir='%s'," % (uploadedFiles['topFile'][1], session['gromacsIncludeDir']))
        script.append("    periodicBoxVectors=gro.getPeriodicBoxVectors()')")

    # System configuration

    script.append('\n# System Configuration\n')
    nonbondedMethod = session['nonbondedMethod']
    script.append('nonbondedMethod = %s' % nonbondedMethod)
    if nonbondedMethod != 'NoCutoff':
        script.append('nonbondedCutoff = %s*nanometers' % session['cutoff'])
    if nonbondedMethod == 'PME':
        script.append('ewaldErrorTolerance = %s' % session['ewaldTol'])
    constraints = session['constraints']
    constraintMethods = {'none': 'None',
                         'water': 'None',
                         'hbonds': 'HBonds',
                         'allbonds': 'AllBonds'}
    script.append('constraints = %s' % constraintMethods[constraints])
    script.append('rigidWater = %s' % ('False' if constraints == 'none' else 'True'))
    if constraints != 'none':
        script.append('constraintTolerance = %s' % session['constraintTol'])

    # Integration options

    script.append('\n# Integration Options\n')
    script.append('dt = %s*picoseconds' % session['dt'])
    ensemble = session['ensemble']
    if ensemble in ('nvt', 'npt'):
        script.append('temperature = %s*kelvin' % session['temperature'])
        script.append('friction = %s/picosecond' % session['friction'])
    if ensemble == 'npt':
        script.append('pressure = %s*atmospheres' % session['pressure'])
        script.append('barostatInterval = %s' % session['barostatInterval'])

    # Simulation options

    script.append('\n# Simulation Options\n')
    script.append('steps = %s' % session['steps'])
    script.append('equilibrationSteps = %s' % session['equilibrationSteps'])
    script.append("platform = Platform.getPlatformByName('%s')" % session['platform'])
    if session['platform'] in ('CUDA', 'OpenCL'):
        script.append("platformProperties = {'Precision': '%s'}" % session['precision'])
    if session['writeDCD']:
        script.append("dcdReporter = DCDReporter('%s', %s)" % (session['dcdFilename'], session['dcdInterval']))
    if session['writeData']:
        args = ', '.join('%s=True' % field for field in session['dataFields'])
        script.append("dataReporter = StateDataReporter('%s', %s, totalSteps=%s," % (session['dataFilename'], session['dataInterval'], session['steps']))
        script.append("    %s, separator='\\t')" % args)
        if isInternal:
            # Create a second reporting sending to stdout so we can display it in the browser.
            script.append("consoleReporter = StateDataReporter(sys.stdout, %s, totalSteps=%s, %s, separator='\\t')" % (session['dataInterval'], session['steps'], args))
    
    # Prepare the simulation
    
    script.append('\n# Prepare the Simulation\n')
    script.append("print('Building system...')")
    if fileType == 'pdb':
        script.append('topology = pdb.topology')
        script.append('positions = pdb.positions')
    elif fileType == 'pdbx':
        script.append('topology = pdbx.topology')
        script.append('positions = pdbx.positions')
    elif fileType == 'amber':
        script.append('topology = prmtop.topology')
        script.append('positions = inpcrd.positions')
    elif fileType == 'gromacs':
        script.append('topology = top.topology')
        script.append('positions = gro.positions')
    if fileType in ('pdb', 'pdbx') and (forcefield == 'charmm_polar_2013.xml' or water in ('tip4pew.xml', 'tip4pfb.xml', 'tip5p.xml')):
        script.append('modeller = Modeller(topology, positions)')
        script.append('modeller.addExtraParticles(forcefield)')
        script.append('topology = modeller.topology')
        script.append('positions = modeller.positions')
    if fileType in ('pdb', 'pdbx'):
        script.append('system = forcefield.createSystem(topology, nonbondedMethod=nonbondedMethod,%s' % (' nonbondedCutoff=nonbondedCutoff,' if nonbondedMethod != 'NoCutoff' else ''))
        script.append('    constraints=constraints, rigidWater=rigidWater%s)' % (', ewaldErrorTolerance=ewaldErrorTolerance' if nonbondedMethod == 'PME' else ''))
    elif fileType == 'amber':
        script.append('system = prmtop.createSystem(nonbondedMethod=nonbondedMethod,%s' % (' nonbondedCutoff=nonbondedCutoff,' if nonbondedMethod != 'NoCutoff' else ''))
        script.append('    constraints=constraints, rigidWater=rigidWater%s)' % (', ewaldErrorTolerance=ewaldErrorTolerance' if nonbondedMethod == 'PME' else ''))
    elif fileType == 'gromacs':
        script.append('system = top.createSystem(nonbondedMethod=nonbondedMethod,%s' % (' nonbondedCutoff=nonbondedCutoff,' if nonbondedMethod != 'NoCutoff' else ''))
        script.append('    constraints=constraints, rigidWater=rigidWater%s)' % (', ewaldErrorTolerance=ewaldErrorTolerance' if nonbondedMethod == 'PME' else ''))
    if ensemble == 'npt':
        script.append('system.addForce(MonteCarloBarostat(pressure, temperature, barostatInterval))')
    if ensemble == 'nve':
        script.append('integrator = VerletIntegrator(dt)')
    else:
        script.append('integrator = LangevinIntegrator(temperature, friction, dt)')
    if constraints != 'none':
        script.append('integrator.setConstraintTolerance(constraintTolerance)')
    script.append('simulation = Simulation(topology, system, integrator, platform%s)' % (', platformProperties' if session['platform'] in ('CUDA', 'OpenCL') else ''))
    script.append('simulation.context.setPositions(positions)')
    if fileType == 'amber':
        script.append('if inpcrd.boxVectors is not None:')
        script.append('    simulation.context.setPeriodicBoxVectors(*inpcrd.boxVectors')
    
    # Minimize and equilibrate
    
    script.append('\n# Minimize and Equilibrate\n')
    script.append("print('Performing energy minimization...')")
    script.append('simulation.minimizeEnergy()')
    script.append("print('Equilibrating...')")
    script.append('simulation.context.setVelocitiesToTemperature(temperature)')
    script.append('simulation.step(equilibrationSteps)')
    
    # Simulate
    
    script.append('\n# Simulate\n')
    script.append("print('Simulating...')")
    if session['writeDCD']:
        script.append('simulation.reporters.append(dcdReporter)')
    if session['writeData']:
        script.append('simulation.reporters.append(dataReporter)')
        if isInternal:
            script.append('simulation.reporters.append(consoleReporter)')
    script.append('simulation.currentStep = 0')
    script.append('simulation.step(steps)')

    return "\n".join(script)
